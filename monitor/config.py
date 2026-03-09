# =============================================================================
#  config.py  —  Monitor configuration
#  Edit this file to point at your robots before running.
# =============================================================================

PORT            = 8000
POLL_INTERVAL   = 0.5    # seconds between polls
HEARTBEAT_TTL   = 5.0    # seconds before a robot is considered dead

# Robots to probe regardless of mDNS discovery.
# Format: { "robot_id": "http://ip:port" }
STATIC_ROBOTS: dict[str, str] = {
    "robot1":  "http://10.40.118.220:8000",
    "robot2":  "http://10.40.108.86:8000",
    "stretch1":"http://10.40.98.25:8000",
}
