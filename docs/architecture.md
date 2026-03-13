# Architecture

## Overview

The system is fully distributed - there is no central server or coordinator. Each robot is simultaneously a **server** (publishes its own state) and a **client** (polls every other robot's state). A separate monitor process polls all robots from a laptop without being part of the robot fleet itself.

---

## Thread Model

Every running instance (`server.py` or `main.py`) is a **single process** containing multiple threads:

```
Process (e.g. python3 main.py)
|
|-- Thread: rclpy.spin(node)       - blocks processing ROS callbacks
|-- Thread: uvicorn.Server.run()   - HTTP server, handles API requests
|-- Thread: poll_peers()           - concurrent HTTP polling loop
|-- Thread: exchange_peer_urls()   - optional peer exchange loop
|-- Thread: _heartbeat_ticker()    - background timer
`-- Main thread: Rich Live loop    - terminal dashboard (main.py only)
```

All threads share memory directly via `state.py`. Concurrent access to shared dicts is guarded by `threading.Lock` objects (`peers_lock`, `peer_urls_lock`, `_log_lock`). `own_state` itself is not locked - it is always written by a single thread (the ROS spin thread via callbacks) and read by multiple threads, which is safe for Python dicts due to the GIL.

When running `server.py` alone there is no poll thread and no dashboard thread - only the ROS spin thread, the uvicorn thread, and the heartbeat ticker.

---

## Data Flow

```
ROS2 topics
    |
    v (_odom_cb, _dock_cb, _battery_cb, etc.)
state.own_state  <- updated in-place
    |
    |--> GET /state (served by uvicorn thread)
    |       |
    |       |  (HTTP, every POLL_INTERVAL)
    |       v
    |   peer robot's poll_peers() loop
    |       |
    |       v
    |   state.peers[robot_id]["state"] (on the polling robot)
    |
    |--> FastAPI middleware stamps `own_state["last_api_query"]`
    |    on operational inbound API requests
    |
    `--> dashboard.build_own_panel() (reads own_state directly)
         dashboard.build_peers_table() (reads state.peers)
```

---

## Discovery: How Robots Find Each Other

There are two complementary discovery mechanisms. Both feed the same `state.peer_urls` dict.

### 1. Static IPs (`STATIC_PEERS` in `config.py`)
Populated at process start. Useful when the network environment is known or when mDNS is unreliable. This is the most predictable option for a lab setting.

### 2. mDNS (`_robot._tcp.local.`)
Each robot advertises itself as `{ROBOT_ID}._robot._tcp.local.` on startup via `zeroconf.ServiceBrowser`. Any other robot or monitor on the same network segment will receive this broadcast and add the IP to `peer_urls`. Unreliable on WiFi networks with AP client isolation or multicast filtering.

### 3. Peer Exchange (`GET /peer_urls`)
Optional. When `PEER_EXCHANGE_ENABLED` is `True`, the client runs a separate background loop that fetches `/peer_urls` from peers it is already reaching successfully every `PEER_EXCHANGE_INTERVAL` seconds. Any robot the peer knows about that is not yet locally known is added to `peer_urls`. This means robots can discover each other transitively through a mutual peer - if robot1 knows both robot2 and stretch, robot2 will discover stretch via robot1's `/peer_urls` response without adding it statically.

**Priority / source of truth:** The last writer wins for any given `robot_id` key in `peer_urls`. In practice the URL for a given robot is stable so this is not a problem.

---

## Liveness and Status

Each robot's `own_state` contains a `heartbeat_ts` field - a Unix float (`time.time()`) updated every `POLL_INTERVAL` seconds by the `_heartbeat_ticker` thread, **independent of ROS**. This means the process can confirm it is alive even if no ROS topics are being received.

However, `heartbeat_ts` is **not used for peer status classification** in the dashboard. Comparing a remote machine's `time.time()` value against local `time.time()` is unreliable because of clock skew - even 1-2 seconds of NTP drift between robots can cause the status to oscillate across thresholds.

Instead, peer status is based on **`elapsed`** - the time since the last successful local HTTP poll, measured with `time.monotonic()`, which is immune to inter-machine clock differences:

```
elapsed = time.monotonic() - peers[robot_id]["last_seen"]

elapsed <= POLL_INTERVAL * 3  -> Online
elapsed <= HEARTBEAT_TTL      -> Degraded
elapsed >  HEARTBEAT_TTL      -> Offline (entry removed from peers dict)
```

The `heartbeat_ts` field is still exposed in `/state` and shown in the own-robot panel as a diagnostic - it confirms the process itself is running even when no ROS data is arriving.

Separately, `last_api_query` records the most recent inbound request to the robot's operational HTTP API. This is useful for confirming that other robots or external tools are actually querying the device. Documentation routes such as `/docs` are intentionally excluded so they do not create false positives in the dashboard.

---

## State Isolation

Each running process has its own in-memory `state.py`. There is **no shared memory between processes**. This is why `server.py` and `main.py` cannot run concurrently on the same robot - they are separate Python processes with separate `own_state` dicts. The server's `own_state` would be populated by ROS; the main.py instance would start with an empty one.

The monitor has its own `state.py` (`monitor/state.py`) which stores `robots` (equivalent to `peers`) and `robot_urls` (equivalent to `peer_urls`), with the same locking pattern. The monitor does not perform peer exchange; it only polls `/state`.

---

## Module Dependency Graph

```
config.py   (no dependencies)
    |
state.py    (imports config)
    |
    |-- server.py    (imports config, state)
    |-- client.py    (imports config, state)
    |-- dashboard.py (imports config, state)
    `-- main.py      (imports all of the above)
```

`main.py` is the only module that imports all others. No circular imports. `config.py` and `state.py` never import from the other application modules.
