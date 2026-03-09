import socket
import threading
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.text import Text

# ─────────────────────────────────────────
#  Config
# ─────────────────────────────────────────
PORT            = 8000
POLL_INTERVAL   = 0.5   # seconds between polls
HEARTBEAT_TTL   = 5.0   # seconds before a robot is considered dead

# Robots to probe regardless of mDNS discovery
# Format: { "robot_id": "http://ip:port" }
STATIC_ROBOTS: dict[str, str] = {
    # "robot1": "http://192.168.1.101:8000",
    # "robot2": "http://192.168.1.102:8000",
    'robot1': 'http://10.40.118.220:8000',
    'robot2': 'http://10.40.108.86:8000',
    'stretch': 'http://10.40.98.25:8000',
}


# ─────────────────────────────────────────
#  Shared State
# ─────────────────────────────────────────

# robot_id -> { "state": {...}, "last_seen": float, "reachable": bool }
robots: dict[str, dict] = {}
robots_lock = threading.Lock()

# robot_id -> url
robot_urls: dict[str, str] = dict(STATIC_ROBOTS)
robot_urls_lock = threading.Lock()


# ─────────────────────────────────────────
#  mDNS Discovery
# ─────────────────────────────────────────
class RobotListener(ServiceListener):
    def add_service(self, zc: Zeroconf, type_: str, name: str):
        info = zc.get_service_info(type_, name)
        if not info:
            return
        robot_id = info.properties.get(b"robot_id", b"").decode()
        if robot_id:
            ip  = socket.inet_ntoa(info.addresses[0])
            url = f"http://{ip}:{info.port}"
            with robot_urls_lock:
                if robot_id not in robot_urls:
                    robot_urls[robot_id] = url
                    print(f"[mDNS] Discovered: {robot_id} at {url}")

    def remove_service(self, zc: Zeroconf, type_: str, name: str):
        pass  # TTL handles removal

    def update_service(self, zc: Zeroconf, type_: str, name: str):
        self.add_service(zc, type_, name)


def start_discovery() -> Zeroconf:
    zc = Zeroconf()
    ServiceBrowser(zc, "_robot._tcp.local.", RobotListener())
    return zc


# ─────────────────────────────────────────
#  Polling Loop
# ─────────────────────────────────────────
def _poll_one(robot_id: str, url: str, now: float) -> tuple[str, dict | None]:
    """Fetch /state from a single robot. Returns (robot_id, state_data) or (robot_id, None) on failure."""
    try:
        resp = requests.get(f"{url}/state", timeout=1.0)
        if resp.status_code == 200:
            return robot_id, resp.json()
    except Exception:
        pass
    return robot_id, None


def poll_robots():
    """
    Every POLL_INTERVAL seconds, fetch /state from all known robots concurrently.
    Liveness is derived from heartbeat_ts inside the state response — no separate
    /heartbeat call, halving requests and eliminating serial blocking.
    """
    with ThreadPoolExecutor(max_workers=32) as pool:
        while True:
            now = time.monotonic()

            with robot_urls_lock:
                snapshot = dict(robot_urls)

            futures = {
                pool.submit(_poll_one, robot_id, url, now): (robot_id, url)
                for robot_id, url in snapshot.items()
            }

            for future in as_completed(futures):
                robot_id, url = futures[future]
                _, state_data = future.result()

                with robots_lock:
                    if state_data is not None:
                        robots[robot_id] = {
                            "state":     state_data,
                            "last_seen": now,
                            "reachable": True,
                            "url":       url,
                        }
                    else:
                        if robot_id in robots:
                            elapsed = now - robots[robot_id]["last_seen"]
                            robots[robot_id]["reachable"] = False
                            if elapsed > HEARTBEAT_TTL:
                                robots[robot_id]["state"] = {}  # clear stale data

            time.sleep(POLL_INTERVAL)


# ─────────────────────────────────────────
#  Rich Dashboard
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
        dt = datetime.fromisoformat(ts)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        if age < 2:
            return f"[green]{age:.1f}s ago[/green]"
        elif age < 10:
            return f"[yellow]{age:.1f}s ago[/yellow]"
        else:
            return f"[red]{age:.1f}s ago[/red]"
    except Exception:
        return ts

def fmt_heartbeat_status(state: dict, elapsed_since_poll: float, heartbeat_ttl: float) -> str:
    """
    Derive status from heartbeat_ts in the state if available (most accurate),
    falling back to elapsed time since our last successful poll.
    """
    hb_ts = state.get("heartbeat_ts")
    if hb_ts is not None:
        age = time.time() - hb_ts
        if age <= POLL_INTERVAL * 3:
            return "[green]● Online[/green]"
        elif age <= heartbeat_ttl:
            return "[yellow]● Degraded[/yellow]"
        else:
            return "[red]● Offline[/red]"
    # Fallback: use our own poll timing
    if elapsed_since_poll <= POLL_INTERVAL * 3:
        return "[green]● Online[/green]"
    elif elapsed_since_poll <= heartbeat_ttl:
        return "[yellow]● Degraded[/yellow]"
    return "[red]● Offline[/red]"

def build_table() -> Table:
    now = time.monotonic()
    table = Table(
        title=f"[bold cyan]Robot Monitor[/bold cyan]  [dim]{datetime.now().strftime('%H:%M:%S')}[/dim]",
        expand=True,
        border_style="bright_black",
    )

    table.add_column("Robot ID",    style="bold white",  min_width=10)
    table.add_column("Status",      min_width=10)
    table.add_column("Namespace",   style="magenta",     min_width=10)
    table.add_column("URL",         style="dim",         min_width=22)
    table.add_column("X",           min_width=8)
    table.add_column("Y",           min_width=8)
    table.add_column("Heading",     min_width=8)
    table.add_column("Docked",      min_width=8)
    table.add_column("Battery",     min_width=9)
    table.add_column("Last Update", min_width=12)

    with robots_lock:
        snapshot = dict(robots)

    # Also show static robots that haven't responded yet
    with robot_urls_lock:
        all_ids = set(robot_urls.keys()) | set(snapshot.keys())

    for robot_id in sorted(all_ids):
        entry = snapshot.get(robot_id)

        if entry is None:
            # Known URL but never responded
            url = robot_urls.get(robot_id, "?")
            table.add_row(robot_id, "[dim]Pending…[/dim]", "[dim]—[/dim]", url, *["[dim]—[/dim]"] * 6)
            continue

        s         = entry.get("state", {})
        reachable = entry.get("reachable", False)
        last_seen = entry.get("last_seen", 0)
        url       = entry.get("url", "?")
        elapsed   = now - last_seen
        namespace = s.get("namespace", "[dim]—[/dim]")

        if reachable:
            status = fmt_heartbeat_status(s, elapsed, HEARTBEAT_TTL)
        elif elapsed <= HEARTBEAT_TTL:
            status = "[yellow]● Degraded[/yellow]"
        else:
            status = "[red]● Offline[/red]"

        table.add_row(
            robot_id,
            status,
            namespace,
            url,
            fmt_float(s.get("x")),
            fmt_float(s.get("y")),
            fmt_float(s.get("heading")),
            fmt_bool(s.get("is_docked"), "Docked", "Free"),
            fmt_battery(s.get("battery_percentage"), s.get("battery_voltage")),
            fmt_timestamp(s.get("last_updated")),
        )

    if not all_ids:
        table.add_row("[dim]No robots found yet…[/dim]", *[""] * 9)

    return table


# ─────────────────────────────────────────
#  Main
# ─────────────────────────────────────────
def main():
    console = Console()

    # Load static robots
    if STATIC_ROBOTS:
        console.print(f"[cyan][static][/cyan] Loaded {len(STATIC_ROBOTS)} static robot(s): {list(STATIC_ROBOTS.keys())}")

    # mDNS discovery
    zc = start_discovery()
    console.print("[cyan][mDNS][/cyan] Listening for robots on local network…")

    # Polling thread
    threading.Thread(target=poll_robots, daemon=True).start()

    # Live dashboard
    try:
        with Live(build_table(), console=console, refresh_per_second=2) as live:
            while True:
                time.sleep(0.5)
                live.update(build_table())
    except KeyboardInterrupt:
        pass
    finally:
        zc.close()
        console.print("\n[dim]Monitor stopped.[/dim]")


if __name__ == "__main__":
    main()
