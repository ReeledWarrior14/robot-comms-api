# =============================================================================
#  client.py  —  Monitor robot discovery and polling (no ROS2 required)
#
#  Responsibilities:
#    - Discover robots via mDNS (_robot._tcp.local.)
#    - Poll /state from all known robots concurrently
#    - Maintain the robots and robot_urls dicts in state.py
# =============================================================================

import socket
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener

import config
import state


# ── mDNS discovery ────────────────────────────────────────────────────────────

class _RobotListener(ServiceListener):
    def add_service(self, zc: Zeroconf, type_: str, name: str):
        info = zc.get_service_info(type_, name)
        if not info:
            return
        robot_id = info.properties.get(b"robot_id", b"").decode()
        if robot_id:
            ip  = socket.inet_ntoa(info.addresses[0])
            url = f"http://{ip}:{info.port}"
            with state.robot_urls_lock:
                if robot_id not in state.robot_urls:
                    state.robot_urls[robot_id] = url
                    print(f"[mDNS] Discovered: {robot_id} at {url}")

    def remove_service(self, zc: Zeroconf, type_: str, name: str):
        pass  # TTL handles removal

    def update_service(self, zc: Zeroconf, type_: str, name: str):
        self.add_service(zc, type_, name)


def start_discovery() -> Zeroconf:
    zc = Zeroconf()
    ServiceBrowser(zc, "_robot._tcp.local.", _RobotListener())
    return zc


# ── Polling ───────────────────────────────────────────────────────────────────

def _poll_one(robot_id: str, url: str) -> tuple[str, dict | None]:
    try:
        resp = requests.get(f"{url}/state", timeout=1.0)
        if resp.status_code == 200:
            return robot_id, resp.json()
    except Exception:
        pass
    return robot_id, None


def poll_robots():
    """
    Fetch /state from all known robots concurrently every POLL_INTERVAL seconds.
    Marks robots unreachable on failure; clears stale state after HEARTBEAT_TTL.
    """
    with ThreadPoolExecutor(max_workers=32) as pool:
        while True:
            now = time.monotonic()

            with state.robot_urls_lock:
                snapshot = dict(state.robot_urls)

            futures = {
                pool.submit(_poll_one, rid, url): (rid, url)
                for rid, url in snapshot.items()
            }

            for future in as_completed(futures):
                robot_id, url = futures[future]
                _, state_data = future.result()

                with state.robots_lock:
                    if state_data is not None:
                        state.robots[robot_id] = {
                            "state":     state_data,
                            "last_seen": now,
                            "reachable": True,
                            "url":       url,
                        }
                    else:
                        if robot_id in state.robots:
                            elapsed = now - state.robots[robot_id]["last_seen"]
                            state.robots[robot_id]["reachable"] = False
                            if elapsed > config.HEARTBEAT_TTL:
                                state.robots[robot_id]["state"] = {}

            time.sleep(config.POLL_INTERVAL)


def start_polling():
    threading.Thread(target=poll_robots, daemon=True).start()
