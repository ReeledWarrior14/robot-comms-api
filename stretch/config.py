# =============================================================================
#  config.py  —  Stretch Hello Robot 3 configuration
#  Edit this file before deploying to each robot.
# =============================================================================

ROBOT_ID        = "stretch1"
NAMESPACE       = ""      # Stretch publishes on global topics — leave blank unless
                           # running a multi-robot namespaced setup
PORT            = 8000
POLL_INTERVAL   = 0.5     # seconds between peer state polls
HEARTBEAT_TTL   = 5.0     # seconds before a peer is considered dead
PEER_EXCHANGE_ENABLED  = True   # query /peer_urls from peers for transitive discovery
PEER_EXCHANGE_INTERVAL = 5.0    # seconds between peer exchange cycles

# Hardcoded peers to poll regardless of mDNS discovery.
# Format: { "robot_id": "http://ip:port" }
STATIC_PEERS: dict[str, str] = {
    # "robot1": "http://192.168.1.101:8000",
    # "stretch2": "http://192.168.1.103:8000",
}

# Stretch 3 battery voltage range for percentage estimation (12V lead-acid).
BATTERY_V_MIN = 11.8   # 0%
BATTERY_V_MAX = 12.7   # 100%
