# =============================================================================
#  main.py  —  Monitor entry point  (no ROS2 required)
#
#  Run from this directory on any laptop/device on the same network:
#
#    python3 main.py
# =============================================================================

import config
import client
import dashboard


def main():
    if config.STATIC_ROBOTS:
        print(
            f"[static] Loaded {len(config.STATIC_ROBOTS)} static robot(s): "
            f"{list(config.STATIC_ROBOTS.keys())}"
        )

    zc = client.start_discovery()
    print("[mDNS] Listening for robots on local network…")

    client.start_polling()

    try:
        dashboard.run_dashboard()
    except KeyboardInterrupt:
        pass
    finally:
        zc.close()
        print("\nMonitor stopped.")


if __name__ == "__main__":
    main()
