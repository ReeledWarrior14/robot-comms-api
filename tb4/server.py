# =============================================================================
#  server.py  —  TurtleBot4 ROS2 subscriber + FastAPI HTTP server
#
#  Responsibilities:
#    - Subscribe to ROS2 topics and update own_state
#    - Expose /state, /peers, /heartbeat, /peers/{id} HTTP endpoints
#    - Advertise this robot via mDNS so peers can discover it
# =============================================================================

import math
import socket
import threading

import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from sensor_msgs.msg import BatteryState
from irobot_create_msgs.msg import DockStatus

from fastapi import FastAPI
from zeroconf import ServiceInfo, Zeroconf
import uvicorn
from datetime import datetime, timezone

import config
import state


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI()


@app.get("/state")
def get_own_state():
    return state.own_state


@app.get("/peers")
def get_all_peers():
    """Returns all known peers and their last state."""
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
    Subscribes to odom, dock_status, and battery_state under the configured
    NAMESPACE and writes updates directly into state.own_state.
    """

    def __init__(self):
        super().__init__("comms_server", namespace=config.NAMESPACE or None)

        self.create_subscription(Odometry,    "odom",          self._odom_cb,    10)
        self.create_subscription(DockStatus,  "dock_status",   self._dock_cb,    10)
        self.create_subscription(BatteryState,"battery_state", self._battery_cb, 10)

    def _odom_cb(self, msg: Odometry):
        q = msg.pose.pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        state.own_state["x"]            = msg.pose.pose.position.x
        state.own_state["y"]            = msg.pose.pose.position.y
        state.own_state["heading"]      = math.atan2(siny_cosp, cosy_cosp)
        state.own_state["last_updated"] = now_iso()

    def _dock_cb(self, msg: DockStatus):
        state.own_state["is_docked"]    = msg.is_docked
        state.own_state["last_updated"] = now_iso()

    def _battery_cb(self, msg: BatteryState):
        # BatteryState.percentage is 0.0–1.0; convert to 0–100
        state.own_state["battery_percentage"] = round(msg.percentage * 100.0, 1)
        state.own_state["last_updated"]       = now_iso()


# ── mDNS advertisement ────────────────────────────────────────────────────────

def advertise_self() -> Zeroconf:
    """Register this robot on the local network via mDNS."""
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
    """Start uvicorn in a daemon thread. Returns the Server so main can stop it."""
    uv_config = uvicorn.Config(app, host="0.0.0.0", port=config.PORT, log_level="warning")
    server = uvicorn.Server(uv_config)
    threading.Thread(target=server.run, daemon=True).start()
    state.log(f"[cyan][api][/cyan] Serving on port {config.PORT}")
    return server


# ── ROS2 launcher ─────────────────────────────────────────────────────────────

def start_ros2() -> RobotStateListener:
    """Initialise rclpy, create node, spin in a daemon thread. Returns the node."""
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
#   python3 server.py --ros-args -r __ns:=/robot2

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
