# =============================================================================
#  client.py  —  Stretch peer discovery and polling
#
#  Responsibilities:
#    - Discover peers via mDNS (_robot._tcp.local.)
#    - Poll /state from all known peers concurrently
#    - Exchange /peer_urls with peers on a separate optional interval
#    - Maintain the peers and peer_urls dicts in state.py
#    - Tick heartbeat_ts in own_state every POLL_INTERVAL seconds
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
        if robot_id and robot_id != config.ROBOT_ID:
            ip  = socket.inet_ntoa(info.addresses[0])
            url = f"http://{ip}:{info.port}"
            with state.peer_urls_lock:
                if robot_id not in state.peer_urls:
                    state.peer_urls[robot_id] = url
                    state.log(f"[cyan][mDNS][/cyan] Discovered peer: [bold]{robot_id}[/bold] at {url}")

    def remove_service(self, zc: Zeroconf, type_: str, name: str):
        pass  # heartbeat TTL handles eviction

    def update_service(self, zc: Zeroconf, type_: str, name: str):
        self.add_service(zc, type_, name)


def start_discovery() -> Zeroconf:
    zc = Zeroconf()
    ServiceBrowser(zc, "_robot._tcp.local.", _RobotListener())
    return zc


# ── Heartbeat ticker ──────────────────────────────────────────────────────────

def _heartbeat_ticker():
    while True:
        state.own_state["heartbeat_ts"] = time.time()
        time.sleep(config.POLL_INTERVAL)


def start_heartbeat_ticker():
    threading.Thread(target=_heartbeat_ticker, daemon=True).start()


# ── Peer polling ──────────────────────────────────────────────────────────────

def _poll_one(robot_id: str, url: str) -> tuple[str, dict | None]:
    """Fetch /state from one peer and return (robot_id, state_data_or_None)."""
    try:
        resp = requests.get(f"{url}/state", timeout=1.0)
        if resp.status_code == 200:
            return robot_id, resp.json()
    except Exception:
        pass
    return robot_id, None


def _fetch_peer_urls(robot_id: str, url: str) -> tuple[str, dict[str, str]]:
    """Fetch /peer_urls from one peer and return discovered URLs, if any."""
    try:
        resp = requests.get(f"{url}/peer_urls", timeout=1.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                return robot_id, data
    except Exception:
        pass
    return robot_id, {}


def _merge_remote_peer_urls(source_robot_id: str, remote_urls: dict[str, str]) -> None:
    """Add newly discovered peers from a remote /peer_urls response."""
    if not remote_urls:
        return
    with state.peer_urls_lock:
        for known_id, known_url in remote_urls.items():
            if known_id != config.ROBOT_ID and known_id not in state.peer_urls:
                state.peer_urls[known_id] = known_url
                state.log(
                    f"[cyan][exchange][/cyan] Discovered [bold]{known_id}[/bold] "
                    f"via {source_robot_id} at {known_url}"
                )


def poll_peers():
    """
    Main polling loop. Fetches /state from all known peers concurrently every
    POLL_INTERVAL seconds.
    """
    with ThreadPoolExecutor(max_workers=16) as pool:
        while True:
            with state.peer_urls_lock:
                snapshot = dict(state.peer_urls)

            futures = {
                pool.submit(_poll_one, rid, url): rid
                for rid, url in snapshot.items()
            }

            for future in as_completed(futures):
                robot_id, state_data = future.result()

                with state.peers_lock:
                    if state_data is not None:
                        state.peers[robot_id] = {
                            "state":     state_data,
                            "last_seen": time.monotonic(),
                        }
                    elif robot_id in state.peers:
                        elapsed = time.monotonic() - state.peers[robot_id]["last_seen"]
                        if elapsed > config.HEARTBEAT_TTL:
                            state.log(
                                f"[red][heartbeat][/red] [bold]{robot_id}[/bold] "
                                f"timed out after {elapsed:.1f}s — removing"
                            )
                            del state.peers[robot_id]

            time.sleep(config.POLL_INTERVAL)


def exchange_peer_urls():
    """
    Background peer exchange loop. Fetches /peer_urls from peers on a separate
    interval so discovery traffic cannot delay state freshness tracking.
    """
    with ThreadPoolExecutor(max_workers=16) as pool:
        while True:
            with state.peer_urls_lock:
                known_urls = dict(state.peer_urls)
            with state.peers_lock:
                reachable_ids = set(state.peers.keys())

            exchange_targets = {
                robot_id: url
                for robot_id, url in known_urls.items()
                if robot_id in reachable_ids
            }

            futures = {
                pool.submit(_fetch_peer_urls, rid, url): rid
                for rid, url in exchange_targets.items()
            }

            for future in as_completed(futures):
                robot_id, remote_urls = future.result()
                _merge_remote_peer_urls(robot_id, remote_urls)

            time.sleep(config.PEER_EXCHANGE_INTERVAL)


def start_polling():
    threading.Thread(target=poll_peers, daemon=True).start()


def start_peer_exchange():
    threading.Thread(target=exchange_peer_urls, daemon=True).start()
