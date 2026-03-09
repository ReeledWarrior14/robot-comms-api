# =============================================================================
#  state.py  —  Shared mutable state for the monitor
# =============================================================================

import threading

import config

# robot_id -> { "state": {...}, "last_seen": float, "reachable": bool, "url": str }
robots: dict[str, dict] = {}
robots_lock = threading.Lock()

# robot_id -> url  (merged from mDNS discovery + STATIC_ROBOTS)
robot_urls: dict[str, str] = dict(config.STATIC_ROBOTS)
robot_urls_lock = threading.Lock()
