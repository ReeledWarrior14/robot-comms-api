# =============================================================================
#  server.py  —  Stretch ROS2 subscriber + FastAPI HTTP server
#
#  Responsibilities:
#    - Subscribe to ROS2 topics and update own_state
#    - Expose /state, /peers, /heartbeat, /peers/{id} HTTP endpoints
#    - Advertise this robot via mDNS so peers can discover it
#
#  Stretch-specific topics:
#    odom           — nav_msgs/Odometry
#    battery        — sensor_msgs/BatteryState  (note: NOT battery_state)
#    is_runstopped  — std_msgs/Bool
# =============================================================================

import math
import socket
import threading

import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from sensor_msgs.msg import BatteryState
from std_msgs.msg import Bool

from fastapi import FastAPI
from zeroconf import ServiceInfo, Zeroconf
import uvicorn
from datetime import datetime, timezone

import config
import state


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_battery_pct(voltage: float) -> float:
    """Linear voltage → percentage map for Stretch 3 12V lead-acid pack."""
    pct = (voltage - config.BATTERY_V_MIN) / (config.BATTERY_V_MAX - config.BATTERY_V_MIN) * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI()


@app.get("/state")
def get_own_state():
    return state.own_state


@app.get("/peers")
def get_all_peers():
    with state.peers_lock:
        return {rid: p["state"] for rid, p in state.peers.items()}


@app.get("/peers/{robot_id}")
def get_peer(robot_id: str):
    with state.peers_lock:
        if robot_id not in state.peers:
            return {"error": f"{robot_id} not found"}
        return state.peers[robot_id]["state"]


@app.get("/peer_urls")
def get_peer_urls():
    """Returns this robot's known peer URLs — used by peers for discovery exchange."""
    with state.peer_urls_lock:
        return dict(state.peer_urls)


@app.get("/heartbeat")
def heartbeat():
    return {"robot_id": config.ROBOT_ID, "alive": True, "timestamp": now_iso()}


# ── ROS2 node ─────────────────────────────────────────────────────────────────

class RobotStateListener(Node):
    """
    Subscribes to Stretch-specific ROS2 topics and writes updates to
    state.own_state. Note: Stretch uses /battery (not /battery_state) and
    exposes is_runstopped instead of dock_status.
    """

    def __init__(self):
        super().__init__("comms_server", namespace=config.NAMESPACE or None)

        self.create_subscription(Odometry,    "odom",          self._odom_cb,       10)
        self.create_subscription(BatteryState,"battery",       self._battery_cb,    10)
        self.create_subscription(Bool,        "is_runstopped", self._runstop_cb,    10)

    def _odom_cb(self, msg: Odometry):
        q = msg.pose.pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        state.own_state["x"]            = msg.pose.pose.position.x
        state.own_state["y"]            = msg.pose.pose.position.y
        state.own_state["heading"]      = math.atan2(siny_cosp, cosy_cosp)
        state.own_state["last_updated"] = now_iso()

    def _battery_cb(self, msg: BatteryState):
        voltage = msg.voltage
        state.own_state["battery_voltage"]    = round(voltage, 3)
        state.own_state["battery_current"]    = round(msg.current, 3)
        # BatteryState.percentage is NaN on Stretch 3 — estimate from voltage
        state.own_state["battery_percentage"] = estimate_battery_pct(voltage)
        state.own_state["last_updated"]       = now_iso()

    def _runstop_cb(self, msg: Bool):
        state.own_state["is_runstopped"] = msg.data
        state.own_state["last_updated"]  = now_iso()


# ── mDNS advertisement ────────────────────────────────────────────────────────

def advertise_self() -> Zeroconf:
    ip = socket.gethostbyname(socket.gethostname())
    info = ServiceInfo(
        "_robot._tcp.local.",
        f"{config.ROBOT_ID}._robot._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=config.PORT,
        properties={"robot_id": config.ROBOT_ID},
    )
    zc = Zeroconf()
    zc.register_service(info)
    state.log(f"[cyan][mDNS][/cyan] Advertising [bold]{config.ROBOT_ID}[/bold] at {ip}:{config.PORT}")
    return zc


# ── Uvicorn launcher ──────────────────────────────────────────────────────────

def start_api_server() -> uvicorn.Server:
    uv_config = uvicorn.Config(app, host="0.0.0.0", port=config.PORT, log_level="warning")
    server = uvicorn.Server(uv_config)
    threading.Thread(target=server.run, daemon=True).start()
    state.log(f"[cyan][api][/cyan] Serving on port {config.PORT}")
    return server


# ── ROS2 launcher ─────────────────────────────────────────────────────────────

def start_ros2() -> RobotStateListener:
    rclpy.init()
    node = RobotStateListener()
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()
    ns_display = config.NAMESPACE or "(none)"
    state.log(f"[cyan][ros2][/cyan] Node started — namespace: [magenta]{ns_display}[/magenta]")
    return node


# ── Standalone entry point ────────────────────────────────────────────────────
# Run server-only (ROS2 + HTTP API + mDNS) with no peer polling or dashboard:
#
#   python3 server.py
#   python3 server.py --ros-args -r __ns:=/stretch2

if __name__ == "__main__":
    import logging
    import time

    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("zeroconf").setLevel(logging.WARNING)

    ros_node   = start_ros2()
    zc_advert  = advertise_self()
    api_server = start_api_server()

    print(f"[server] {config.ROBOT_ID} running — API on port {config.PORT}. Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        api_server.should_exit = True
        zc_advert.unregister_all_services()
        zc_advert.close()
        rclpy.shutdown()
