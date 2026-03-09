# =============================================================================
#  config.py  —  TurtleBot4 robot configuration
#  Edit this file before deploying to each robot.
# =============================================================================

ROBOT_ID        = "robot1"
NAMESPACE       = "/robot1"   # ROS2 namespace — set to "" for none
PORT            = 8000
POLL_INTERVAL   = 0.5         # seconds between peer state polls
HEARTBEAT_TTL   = 5.0         # seconds before a peer is considered dead

# Hardcoded peers to poll regardless of mDNS discovery.
# Format: { "robot_id": "http://ip:port" }
STATIC_PEERS: dict[str, str] = {
    # "robot2": "http://192.168.1.101:8000",
    # "stretch1": "http://192.168.1.102:8000",
}
