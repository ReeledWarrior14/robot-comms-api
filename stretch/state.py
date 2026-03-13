# =============================================================================
#  state.py  - Shared mutable state for Stretch comms node
#  Imported by server.py, client.py, dashboard.py, and main.py.
# =============================================================================

import threading
from collections import deque
from datetime import datetime

import config

# Own robot state (written by server.py, read by API and dashboard)
own_state: dict = {
    "robot_id":           config.ROBOT_ID,
    "namespace":          config.NAMESPACE or "(none)",
    "x":                  None,
    "y":                  None,
    "heading":            None,
    "battery_voltage":    None,   # volts (raw)
    "battery_current":    None,   # amps; positive = discharging
    "battery_percentage": None,   # estimated from voltage (lead-acid linear map)
    "is_runstopped":      None,   # True = e-stop / runstop engaged
    "joint_state": {               # raw JointState payload snapshot
        "name": [],
        "position": [],
        "velocity": [],
        "effort": [],
        "header_stamp": None,      # ISO 8601 UTC from msg.header.stamp
    },
    "joints":             {},      # joint_name -> {position, velocity, effort}
    "last_updated":       None,    # ISO 8601 timestamp
    "last_api_query":     None,    # ISO 8601 timestamp of last inbound API hit
    "heartbeat_ts":       None,    # Unix float, ticked by client heartbeat thread
}

# Peer state (written by client.py, read by API and dashboard)
# robot_id -> { "state": {...}, "last_seen": float (monotonic) }
peers: dict[str, dict] = {}
peers_lock = threading.Lock()

# robot_id -> url (merged from mDNS discovery + STATIC_PEERS)
peer_urls: dict[str, str] = dict(config.STATIC_PEERS)
peer_urls_lock = threading.Lock()

# Rolling log buffer (written by log(), read by dashboard)
_log_buffer: deque[str] = deque(maxlen=12)
_log_lock = threading.Lock()


def log(msg: str) -> None:
    """Append a Rich-markup log line to the ring buffer."""
    ts = datetime.now().strftime("%H:%M:%S")
    with _log_lock:
        _log_buffer.append(f"[dim]{ts}[/dim] {msg}")


def get_log_lines() -> list[str]:
    """Return a snapshot of the current log buffer."""
    with _log_lock:
        return list(_log_buffer)
