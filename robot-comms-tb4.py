# =============================================================================
#  robot-comms.py  —  TurtleBot4 distributed inter-robot communication node
# =============================================================================
#
#  RUNNING
#  -------
#  You do NOT need `ros2 run`. Run directly as a normal Python file, as long
#  as your ROS2 environment is sourced first:
#
#    source /opt/ros/humble/setup.bash          # or jazzy / iron etc.
#    source ~/your_ws/install/setup.bash        # your workspace, if needed
#    python3 robot-comms.py
#
#  To override the namespace at runtime without editing the file:
#
#    python3 robot-comms.py --ros-args -r __ns:=/robot2
#
#  CONFIGURATION
#  -------------
#  Edit the Config section below before deploying to each robot:
#    ROBOT_ID   — unique name for this robot (used in mDNS and API responses)
#    NAMESPACE  — ROS2 topic namespace, e.g. "/robot1"  ("" to disable)
#    PORT       — HTTP port this robot's API listens on
#    STATIC_PEERS — optional dict of known peer IPs to poll unconditionally
#
#  DEPENDENCIES
#  ------------
#    pip install fastapi uvicorn zeroconf requests rich
#    ros-<distro>-irobot-create-msgs  (for DockStatus)
# =============================================================================

import socket
import threading
import time
import math
import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from sensor_msgs.msg import BatteryState
from irobot_create_msgs.msg import DockStatus
from fastapi import FastAPI
from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser, ServiceListener
import uvicorn
import requests
from datetime import datetime, timezone
from rich.console import Console
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.live import Live
from rich.text import Text

# ─────────────────────────────────────────
#  Config — edit these per robot
# ─────────────────────────────────────────
ROBOT_ID        = "robot1"
NAMESPACE       = "/robot1"  # ROS2 namespace — set to "" for none, e.g. "/robot1"
PORT            = 8000
POLL_INTERVAL   = 0.5   # seconds between state polls
HEARTBEAT_TTL   = 5.0   # seconds before a peer is considered dead

# Optional: hardcoded peers to poll regardless of mDNS discovery
# Format: { "robot_id": "http://ip:port" }
STATIC_PEERS: dict[str, str] = {
    # "robot2": "http://192.168.1.101:8000",
    # "robot3": "http://192.168.1.102:8000",
}


# ─────────────────────────────────────────
#  Shared State
# ─────────────────────────────────────────
def now_iso() -> str:
    """Returns current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()

own_state: dict = {
    "robot_id":           ROBOT_ID,
    "namespace":          NAMESPACE or "(none)",
    "x":                  None,
    "y":                  None,
    "heading":            None,
    "is_docked":          None,   # True / False / None (unknown)
    "battery_percentage": None,   # 0.0 - 100.0 / None (unknown)
    "last_updated":       None,   # ISO 8601 timestamp, updated on any ROS callback
    "heartbeat_ts":       None,   # Unix timestamp (float), ticks every POLL_INTERVAL
                                  # regardless of ROS activity — use this to confirm
                                  # the process itself is alive
}

# robot_id -> { "state": {...}, "last_seen": float }
peers: dict[str, dict] = {}
peers_lock = threading.Lock()

# robot_id -> url  (merged from mDNS + static config)
peer_urls: dict[str, str] = dict(STATIC_PEERS)
peer_urls_lock = threading.Lock()

# Recent log messages for the dashboard
_log_buffer: deque[str] = deque(maxlen=12)
_log_lock = threading.Lock()

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    with _log_lock:
        _log_buffer.append(f"[dim]{ts}[/dim] {msg}")


def _heartbeat_ticker():
    """Updates heartbeat_ts every POLL_INTERVAL seconds, independent of ROS."""
    while True:
        own_state["heartbeat_ts"] = time.time()
        time.sleep(POLL_INTERVAL)


# ─────────────────────────────────────────
#  REST Endpoints
# ─────────────────────────────────────────
app = FastAPI()

@app.get("/state")
def get_own_state():
    return own_state

@app.get("/peers")
def get_all_peers():
    """Returns all alive peers and their last known state."""
    with peers_lock:
        return {rid: p["state"] for rid, p in peers.items()}

@app.get("/peers/{robot_id}")
def get_peer(robot_id: str):
    """Returns the last known state of a specific peer."""
    with peers_lock:
        if robot_id not in peers:
            return {"error": f"{robot_id} not found"}
        return peers[robot_id]["state"]

@app.get("/heartbeat")
def heartbeat():
    """Lightweight endpoint polled by peers to check liveness."""
    return {"robot_id": ROBOT_ID, "alive": True, "timestamp": now_iso()}


# ─────────────────────────────────────────
#  ROS2 Node
# ─────────────────────────────────────────
class RobotStateListener(Node):
    def __init__(self):
        # Passing namespace here makes all relative topic names resolve under it.
        # e.g. NAMESPACE="/robot1" + topic "odom" -> /robot1/odom
        # Users can also override at launch: ros2 run ... --ros-args -r __ns:=/robot1
        super().__init__("coords_server", namespace=NAMESPACE or None)

        self.create_subscription(
            Odometry,
            "odom",          # relative — resolves to <namespace>/odom
            self.odom_callback,
            10
        )
        self.create_subscription(
            DockStatus,
            "dock_status",   # relative — resolves to <namespace>/dock_status
            self.dock_callback,
            10
        )
        self.create_subscription(
            BatteryState,
            "battery_state", # relative — resolves to <namespace>/battery_state
            self.battery_callback,
            10
        )

    def odom_callback(self, msg: Odometry):
        q = msg.pose.pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        own_state["x"]            = msg.pose.pose.position.x
        own_state["y"]            = msg.pose.pose.position.y
        own_state["heading"]      = math.atan2(siny_cosp, cosy_cosp)
        own_state["last_updated"] = now_iso()

    def dock_callback(self, msg: DockStatus):
        own_state["is_docked"]    = msg.is_docked
        own_state["last_updated"] = now_iso()

    def battery_callback(self, msg: BatteryState):
        # BatteryState.percentage is 0.0–1.0, convert to 0–100
        own_state["battery_percentage"] = round(msg.percentage * 100.0, 1)
        own_state["last_updated"]       = now_iso()


# ─────────────────────────────────────────
#  mDNS Advertisement
# ─────────────────────────────────────────
def advertise_self() -> Zeroconf:
    ip = socket.gethostbyname(socket.gethostname())
    info = ServiceInfo(
        "_robot._tcp.local.",
        f"{ROBOT_ID}._robot._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=PORT,
        properties={"robot_id": ROBOT_ID},
    )
    zc = Zeroconf()
    zc.register_service(info)
    log(f"[cyan][mDNS][/cyan] Advertising [bold]{ROBOT_ID}[/bold] at {ip}:{PORT}")
    return zc


# ─────────────────────────────────────────
#  mDNS Discovery
# ─────────────────────────────────────────
class RobotListener(ServiceListener):
    def add_service(self, zc: Zeroconf, type_: str, name: str):
        info = zc.get_service_info(type_, name)
        if not info:
            return
        robot_id = info.properties.get(b"robot_id", b"").decode()
        if robot_id and robot_id != ROBOT_ID:
            ip  = socket.inet_ntoa(info.addresses[0])
            url = f"http://{ip}:{info.port}"
            with peer_urls_lock:
                peer_urls[robot_id] = url
            log(f"[cyan][mDNS][/cyan] Discovered peer: [bold]{robot_id}[/bold] at {url}")

    def remove_service(self, zc: Zeroconf, type_: str, name: str):
        # Heartbeat TTL handles removal — mDNS signal is a bonus
        pass

    def update_service(self, zc: Zeroconf, type_: str, name: str):
        self.add_service(zc, type_, name)


def start_discovery() -> Zeroconf:
    zc = Zeroconf()
    ServiceBrowser(zc, "_robot._tcp.local.", RobotListener())
    return zc


# ─────────────────────────────────────────
#  Peer Polling + Heartbeat
# ─────────────────────────────────────────
def _poll_one(robot_id: str, url: str, now: float) -> tuple[str, dict | None]:
    """Fetch /state from a single peer. Returns (robot_id, state_data) or (robot_id, None) on failure."""
    try:
        resp = requests.get(f"{url}/state", timeout=1.0)
        if resp.status_code == 200:
            return robot_id, resp.json()
    except Exception:
        pass
    return robot_id, None


def poll_peers():
    """
    Every POLL_INTERVAL seconds, fetch /state from all known peers concurrently.
    Liveness is determined from heartbeat_ts inside the state — no separate
    /heartbeat call needed, which halves network requests and removes serial blocking.
    """
    with ThreadPoolExecutor(max_workers=16) as pool:
        while True:
            now = time.monotonic()

            with peer_urls_lock:
                snapshot = dict(peer_urls)

            futures = {
                pool.submit(_poll_one, robot_id, url, now): robot_id
                for robot_id, url in snapshot.items()
            }

            for future in as_completed(futures):
                robot_id, state_data = future.result()

                with peers_lock:
                    if state_data is not None:
                        peers[robot_id] = {
                            "state":     state_data,
                            "last_seen": now,
                        }
                    elif robot_id in peers:
                        elapsed = now - peers[robot_id]["last_seen"]
                        if elapsed > HEARTBEAT_TTL:
                            log(f"[red][heartbeat][/red] [bold]{robot_id}[/bold] timed out after {elapsed:.1f}s — removing")
                            del peers[robot_id]

            time.sleep(POLL_INTERVAL)


# ─────────────────────────────────────────
#  Dashboard
# ─────────────────────────────────────────
def fmt_float(val, decimals: int = 3) -> str:
    if val is None:
        return "[dim]N/A[/dim]"
    return f"{val:.{decimals}f}"

def fmt_bool(val, true_str: str, false_str: str) -> str:
    if val is None:
        return "[dim]N/A[/dim]"
    return f"[green]{true_str}[/green]" if val else f"[yellow]{false_str}[/yellow]"

def fmt_battery(pct, voltage=None) -> str:
    if pct is None and voltage is None:
        return "[dim]N/A[/dim]"
    color = "green" if (pct or 0) >= 60 else ("yellow" if (pct or 0) >= 25 else "red")
    pct_str = f"{pct:.1f}%" if pct is not None else "?%"
    v_str   = f" [dim]{voltage:.2f}V[/dim]" if voltage is not None else ""
    return f"[{color}]{pct_str}[/{color}]{v_str}"

def fmt_timestamp(ts: str | None) -> str:
    if not ts:
        return "[dim]N/A[/dim]"
    try:
        dt  = datetime.fromisoformat(ts)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        color = "green" if age < 2 else ("yellow" if age < 10 else "red")
        return f"[{color}]{age:.1f}s ago[/{color}]"
    except Exception:
        return ts

def build_own_panel() -> Panel:
    s = own_state
    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column(style="dim",        min_width=18)
    table.add_column(style="bold white")
    table.add_row("Robot ID",    f"[bold cyan]{s['robot_id']}[/bold cyan]")
    table.add_row("Namespace",   f"[magenta]{s['namespace']}[/magenta]")
    table.add_row("API Port",    str(PORT))
    table.add_row("X",           fmt_float(s.get("x")))
    table.add_row("Y",           fmt_float(s.get("y")))
    table.add_row("Heading",     fmt_float(s.get("heading")))
    table.add_row("Docked",      fmt_bool(s.get("is_docked"), "Docked", "Free"))
    table.add_row("Battery",     fmt_battery(s.get("battery_percentage")))
    table.add_row("Last Updated", fmt_timestamp(s.get("last_updated")))
    hb = s.get("heartbeat_ts")
    hb_str = f"[green]{hb:.3f}[/green]" if hb else "[dim]N/A[/dim]"
    table.add_row("Heartbeat TS", hb_str)
    return Panel(table, title="[bold green]● This Robot[/bold green]", border_style="green")

def build_peers_table() -> Table:
    now = time.monotonic()
    table = Table(expand=True, border_style="bright_black")
    table.add_column("Robot ID",    style="bold white", min_width=10)
    table.add_column("Status",      min_width=10)
    table.add_column("URL",         style="dim",        min_width=22)
    table.add_column("X",           min_width=8)
    table.add_column("Y",           min_width=8)
    table.add_column("Heading",     min_width=8)
    table.add_column("Docked",      min_width=8)
    table.add_column("Battery",     min_width=9)
    table.add_column("Last Update", min_width=12)

    with peer_urls_lock:
        all_ids = set(peer_urls.keys())
    with peers_lock:
        snapshot = dict(peers)
    all_ids |= set(snapshot.keys())

    for robot_id in sorted(all_ids):
        url   = peer_urls.get(robot_id, "?")
        entry = snapshot.get(robot_id)
        if entry is None:
            table.add_row(robot_id, "[dim]Pending…[/dim]", url, *["[dim]—[/dim]"] * 6)
            continue
        s       = entry.get("state", {})
        elapsed = now - entry.get("last_seen", 0)
        if elapsed <= POLL_INTERVAL * 3:
            status = "[green]● Online[/green]"
        elif elapsed <= HEARTBEAT_TTL:
            status = "[yellow]● Degraded[/yellow]"
        else:
            status = "[red]● Offline[/red]"
        table.add_row(
            robot_id, status, url,
            fmt_float(s.get("x")), fmt_float(s.get("y")), fmt_float(s.get("heading")),
            fmt_bool(s.get("is_docked"), "Docked", "Free"),
            fmt_battery(s.get("battery_percentage"), s.get("battery_voltage")),
            fmt_timestamp(s.get("last_updated")),
        )

    if not all_ids:
        table.add_row("[dim]No peers discovered yet…[/dim]", *[""] * 8)

    return table

def build_log_panel() -> Panel:
    with _log_lock:
        lines = list(_log_buffer)
    text = Text.from_markup("\n".join(lines) if lines else "[dim]No events yet.[/dim]")
    return Panel(text, title="[bold]Log[/bold]", border_style="bright_black")

def build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="top",  ratio=2),
        Layout(name="log",  ratio=1),
    )
    layout["top"].split_row(
        Layout(build_own_panel(),   name="self",  ratio=1),
        Layout(Panel(build_peers_table(), title="[bold cyan]Peers[/bold cyan]", border_style="cyan"),
               name="peers", ratio=3),
    )
    layout["log"].update(build_log_panel())
    return layout


# ─────────────────────────────────────────
#  Main
# ─────────────────────────────────────────
def main():
    # Silence uvicorn and zeroconf log spam so it doesn't bleed into Rich
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("zeroconf").setLevel(logging.WARNING)

    # ROS2
    rclpy.init()
    node = RobotStateListener()
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()
    ns_display = NAMESPACE or "(none)"
    log(f"[cyan][ros2][/cyan] Node started \u2014 namespace: [magenta]{ns_display}[/magenta]")

    # mDNS
    zc_advert    = advertise_self()
    zc_discovery = start_discovery()

    if STATIC_PEERS:
        log(f"[cyan][static][/cyan] Loaded {len(STATIC_PEERS)} static peer(s): {list(STATIC_PEERS.keys())}")

    # Heartbeat timestamp ticker (independent of ROS)
    threading.Thread(target=_heartbeat_ticker, daemon=True).start()

    # Polling + heartbeat loop
    threading.Thread(target=poll_peers, daemon=True).start()

    # Uvicorn in a background thread so Rich can own the main thread
    uv_config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="warning")
    uv_server = uvicorn.Server(uv_config)
    threading.Thread(target=uv_server.run, daemon=True).start()
    log(f"[cyan][api][/cyan] Serving on port {PORT}")

    # Live dashboard
    console = Console()
    try:
        with Live(build_layout(), console=console, refresh_per_second=2) as live:
            while True:
                time.sleep(0.5)
                live.update(build_layout())
    except KeyboardInterrupt:
        pass
    finally:
        uv_server.should_exit = True
        zc_advert.unregister_all_services()
        zc_advert.close()
        zc_discovery.close()
        rclpy.shutdown()


if __name__ == "__main__":
    main()