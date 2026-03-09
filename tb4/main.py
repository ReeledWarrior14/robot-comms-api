# =============================================================================
#  main.py  —  TurtleBot4 comms node entry point
#
#  Run from this directory after sourcing your ROS2 environment:
#
#    source /opt/ros/humble/setup.bash
#    python3 main.py
#
#  Override namespace at launch without editing config.py:
#
#    python3 main.py --ros-args -r __ns:=/robot2
# =============================================================================

import logging
import socket
import sys

import config
import state
import server
import client
import dashboard


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    if _port_in_use(config.PORT):
        print(
            f"ERROR: port {config.PORT} is already in use.\n"
            f"server.py is probably running as a background/startup process.\n"
            f"Stop it first (e.g. 'sudo systemctl stop robot-comms'), then re-run main.py."
        )
        sys.exit(1)

    # Suppress log spam from uvicorn and zeroconf
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("zeroconf").setLevel(logging.WARNING)

    # ROS2 — subscribe to topics, update own_state
    ros_node = server.start_ros2()

    # mDNS — advertise self + discover peers
    zc_advert    = server.advertise_self()
    zc_discovery = client.start_discovery()

    if config.STATIC_PEERS:
        state.log(
            f"[cyan][static][/cyan] Loaded {len(config.STATIC_PEERS)} static peer(s): "
            f"{list(config.STATIC_PEERS.keys())}"
        )

    # Heartbeat ticker — keeps own heartbeat_ts current independent of ROS
    client.start_heartbeat_ticker()

    # Peer polling — concurrent /state fetches from all known peers
    client.start_polling()

    # HTTP API — serves /state, /peers, /heartbeat
    api_server = server.start_api_server()

    # Live dashboard — blocks until Ctrl-C
    try:
        dashboard.run_dashboard()
    except KeyboardInterrupt:
        pass
    finally:
        api_server.should_exit = True
        zc_advert.unregister_all_services()
        zc_advert.close()
        zc_discovery.close()
        import rclpy
        rclpy.shutdown()


if __name__ == "__main__":
    main()
