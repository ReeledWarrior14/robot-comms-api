# =============================================================================
#  client.py  —  TurtleBot4 peer discovery and polling
#
#  Responsibilities:
#    - Discover peers via mDNS (_robot._tcp.local.)
#    - Poll /state from all known peers concurrently
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
    """Start mDNS browser. Returns Zeroconf instance for later cleanup."""
    zc = Zeroconf()
    ServiceBrowser(zc, "_robot._tcp.local.", _RobotListener())
    return zc


# ── Heartbeat ticker ──────────────────────────────────────────────────────────

def _heartbeat_ticker():
    """Update own_state heartbeat_ts every POLL_INTERVAL seconds."""
    while True:
        state.own_state["heartbeat_ts"] = time.time()
        time.sleep(config.POLL_INTERVAL)


def start_heartbeat_ticker():
    threading.Thread(target=_heartbeat_ticker, daemon=True).start()


# ── Peer polling ──────────────────────────────────────────────────────────────

def _poll_one(robot_id: str, url: str) -> tuple[str, dict | None, dict[str, str]]:
    """
    Fetch /state from one peer. If reachable, also fetches /peer_urls for
    peer-exchange discovery (so robots can find each other through a mutual peer).
    Returns (robot_id, state_data_or_None, remote_peer_urls_or_empty).
    """
    state_data: dict | None = None
    remote_urls: dict[str, str] = {}
    try:
        resp = requests.get(f"{url}/state", timeout=1.0)
        if resp.status_code == 200:
            state_data = resp.json()
    except Exception:
        pass
    if state_data is not None:
        try:
            resp2 = requests.get(f"{url}/peer_urls", timeout=1.0)
            if resp2.status_code == 200:
                remote_urls = resp2.json()
        except Exception:
            pass
    return robot_id, state_data, remote_urls


def poll_peers():
    """
    Main polling loop. Fetches /state (and /peer_urls for discovery exchange)
    from all known peers concurrently every POLL_INTERVAL seconds.

    Peer exchange: if peer A knows about peer C that we don't, we add C to our
    peer_urls automatically. This ensures all robots find each other even when
    mDNS multicast doesn't reach every pair directly.
    """
    with ThreadPoolExecutor(max_workers=16) as pool:
        while True:
            now = time.monotonic()

            with state.peer_urls_lock:
                snapshot = dict(state.peer_urls)

            futures = {
                pool.submit(_poll_one, rid, url): rid
                for rid, url in snapshot.items()
            }

            for future in as_completed(futures):
                robot_id, state_data, remote_urls = future.result()

                # Peer exchange: register any robots our peer knows that we don't
                if remote_urls:
                    with state.peer_urls_lock:
                        for known_id, known_url in remote_urls.items():
                            if known_id != config.ROBOT_ID and known_id not in state.peer_urls:
                                state.peer_urls[known_id] = known_url
                                state.log(
                                    f"[cyan][exchange][/cyan] Discovered [bold]{known_id}[/bold] "
                                    f"via {robot_id} at {known_url}"
                                )

                with state.peers_lock:
                    if state_data is not None:
                        state.peers[robot_id] = {
                            "state":     state_data,
                            "last_seen": now,
                        }
                    elif robot_id in state.peers:
                        elapsed = now - state.peers[robot_id]["last_seen"]
                        if elapsed > config.HEARTBEAT_TTL:
                            state.log(
                                f"[red][heartbeat][/red] [bold]{robot_id}[/bold] "
                                f"timed out after {elapsed:.1f}s — removing"
                            )
                            del state.peers[robot_id]

            time.sleep(config.POLL_INTERVAL)


def start_polling():
    threading.Thread(target=poll_peers, daemon=True).start()
