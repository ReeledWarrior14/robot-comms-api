# Robot Fleet Communication System

Distributed inter-robot communication for a mixed **TurtleBot4** and **Stretch Hello Robot 3** fleet running ROS2. No central coordinator — each robot runs its own HTTP API, discovers peers via mDNS and/or static IPs, and polls peer state concurrently. A separate monitor script runs on any device (no ROS2 needed).

---

## Documentation

Detailed documentation is in the [`docs/`](docs/) folder:

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Thread model, data flow, discovery mechanisms, liveness logic, module dependency graph |
| [docs/code-reference.md](docs/code-reference.md) | Every module, class, function, and state variable explained in detail |
| [docs/api-reference.md](docs/api-reference.md) | All HTTP endpoints — request/response schemas, field reference, error behaviour, curl and Python examples |
| [docs/extending.md](docs/extending.md) | How to add new ROS topics, new API endpoints, new robot types, change poll rates, add file logging, and more |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Symptoms, causes, and fixes for common problems (connectivity, stale state, mDNS, flapping status, and more) |
| [docs/guides.md](docs/guides.md) | Systemd service setup and testing without physical robots |

---



```
comms_test/
├── tb4/            # Deploy to each TurtleBot4
│   ├── config.py   ← edit this per robot before deploying
│   ├── state.py
│   ├── server.py
│   ├── client.py
│   ├── dashboard.py
│   └── main.py
│
├── stretch/        # Deploy to each Stretch Hello Robot 3
│   ├── config.py   ← edit this per robot before deploying
│   ├── state.py
│   ├── server.py
│   ├── client.py
│   ├── dashboard.py
│   └── main.py
│
└── monitor/        # Run on any laptop/device — no ROS2 needed
    ├── config.py   ← add robot IPs here
    ├── state.py
    ├── client.py
    ├── dashboard.py
    └── main.py
```

---

## Dependencies

```bash
pip install fastapi uvicorn zeroconf requests rich
```

ROS2 packages (on robot machines only):
```bash
# TurtleBot4 only
sudo apt install ros-<distro>-irobot-create-msgs

# Both TB4 and Stretch
sudo apt install ros-<distro>-nav2-msgs   # provides nav_msgs
```

---

## Configuration

**Before deploying, edit only `config.py` in the relevant folder.**

### `tb4/config.py`

| Variable | Description |
|---|---|
| `ROBOT_ID` | Unique name for this robot, e.g. `"robot1"` |
| `NAMESPACE` | ROS2 namespace, e.g. `"/robot1"` (set `""` to disable) |
| `PORT` | HTTP API port (default `8000`) |
| `POLL_INTERVAL` | Seconds between peer state polls (default `0.5`) |
| `HEARTBEAT_TTL` | Seconds before a peer is considered dead (default `5.0`) |
| `STATIC_PEERS` | Dict of known peer IPs, e.g. `{"robot2": "http://10.0.0.2:8000"}` |

### `stretch/config.py`

Same as TB4 config, plus:

| Variable | Description |
|---|---|
| `BATTERY_V_MIN` | Voltage at 0% (default `11.8` V) |
| `BATTERY_V_MAX` | Voltage at 100% (default `12.7` V) |

> Stretch 3's `BatteryState.percentage` is always NaN — percentage is estimated linearly from voltage.

### `monitor/config.py`

| Variable | Description |
|---|---|
| `STATIC_ROBOTS` | Dict of all robot IPs to poll, e.g. `{"robot1": "http://10.0.0.1:8000"}` |
| `POLL_INTERVAL` | Seconds between polls |
| `HEARTBEAT_TTL` | Seconds before a robot is considered dead |

> mDNS discovery runs automatically in addition to static entries.

---

## Running

### Everything at once (recommended for development)

Starts ROS2 listener, HTTP API, mDNS, peer polling, and the live dashboard in one command.

```bash
# TurtleBot4
cd tb4/
source /opt/ros/humble/setup.bash      # adjust distro as needed
source ~/your_ws/install/setup.bash    # your workspace, if needed
python3 main.py

# Stretch
cd stretch/
source /opt/ros/humble/setup.bash
source ~/ament_ws/install/setup.bash   # if needed
python3 main.py
```

Override namespace at launch without editing `config.py`:
```bash
python3 main.py --ros-args -r __ns:=/robot2
```

---

### Server only (recommended for robot startup / headless operation)

Runs only the ROS2 subscriber + HTTP API + mDNS advertisement.
No peer polling, no terminal dashboard — ideal for a `systemd` service or startup script.

```bash
# TurtleBot4
cd tb4/
source /opt/ros/humble/setup.bash
python3 server.py

# Stretch
cd stretch/
source /opt/ros/humble/setup.bash
python3 server.py
```

With namespace override:
```bash
python3 server.py --ros-args -r __ns:=/robot2
```

Once the server is running, any other robot or the monitor can poll its `/state` endpoint over the network. The robot's own state is always available at:

```
http://<robot-ip>:<PORT>/state
```

---

### Full view on demand (from the robot)

`main.py` and `server.py` **cannot run at the same time on the same robot** — they are separate processes and will conflict:

- uvicorn will fail to bind the port (`Address already in use`)
- zeroconf will error on the duplicate mDNS registration
- `main.py` starts with empty `own_state` (it can't share ROS data with the existing `server.py` process)

`main.py` will detect this and print a clear error instead of crashing silently.

**Stop the background server first, then run main.py:**

```bash
# If running via systemd:
sudo systemctl stop robot-comms

# If running manually in another terminal:
# Ctrl-C that terminal, or: kill $(lsof -ti:8000)

python3 main.py
```

When you're done and want the always-on server back:
```bash
sudo systemctl start robot-comms
```

---

### Monitor — watch the whole fleet from a laptop (no ROS2 needed)

Polls all robots concurrently and shows a live fleet table. Works regardless of whether each robot is running `server.py` or `main.py`.

```bash
cd monitor/
python3 main.py
```

---

### Typical deployment workflow

| Where | What to run | When |
|---|---|---|
| Each robot (always-on) | `python3 server.py` | On boot via systemd / rc.local |
| Laptop / workstation | `python3 monitor/main.py` | Whenever you want the fleet view |
| Robot (debugging) | `python3 main.py` | When you want per-robot peer dashboard |

---

## REST API

Every robot exposes the following endpoints on its configured `PORT`:

| Endpoint | Description |
|---|---|
| `GET /state` | This robot's own state (position, battery, etc.) |
| `GET /peers` | All known peers and their last state |
| `GET /peer_urls` | Known peer URLs — fetched by other robots for discovery exchange |
| `GET /peers/{robot_id}` | Single peer's last state |
| `GET /heartbeat` | Liveness check — returns `{robot_id, alive, timestamp}` |

Example:
```bash
curl http://10.0.0.1:8000/state
curl http://10.0.0.1:8000/peers
curl http://10.0.0.1:8000/heartbeat
```

---

## State Schema

Fields present on all robots:

| Field | Type | Description |
|---|---|---|
| `robot_id` | string | Unique robot name |
| `namespace` | string | ROS2 namespace in use |
| `x`, `y` | float \| null | Position from odometry (metres) |
| `heading` | float \| null | Yaw in radians |
| `battery_percentage` | float \| null | 0–100 |
| `last_updated` | ISO 8601 \| null | Timestamp of last ROS callback |
| `last_api_query` | ISO 8601 \| null | Timestamp of last inbound API request to this robot |
| `heartbeat_ts` | float \| null | Unix timestamp, ticked every `POLL_INTERVAL` regardless of ROS |

TurtleBot4 additional fields:

| Field | Type | Description |
|---|---|---|
| `is_docked` | bool \| null | True if on the dock |

Stretch additional fields:

| Field | Type | Description |
|---|---|---|
| `battery_voltage` | float \| null | Raw voltage (V) |
| `battery_current` | float \| null | Raw current (A); positive = discharging |
| `is_runstopped` | bool \| null | True if e-stop / runstop is engaged |
| `joint_state` | object | Raw `sensor_msgs/JointState` snapshot (`name`, `position`, `velocity`, `effort`, `header_stamp`) |
| `joints` | object | Per-joint lookup map: `{joint_name: {position, velocity, effort}}` |
---

## How Liveness Works

Each robot ticks `heartbeat_ts` (a Unix float) in its own state every `POLL_INTERVAL` seconds, independent of ROS. However, **`heartbeat_ts` is only used for local diagnostics** (visible in the robot's own dashboard panel). It is NOT used to determine peer status.

Each robot also records `last_api_query` whenever its operational HTTP API is queried (`/state`, `/peers`, `/peers/{robot_id}`, `/peer_urls`, `/heartbeat`). In the robot dashboard, `last_updated` is shown as **Last ROS Callback** and `last_api_query` is shown as **Last API Query**.

Peer status is derived from **`elapsed`** — the time since the last successful HTTP poll on the local machine's monotonic clock. This avoids clock skew errors: comparing a remote `time.time()` timestamp against a local `time.time()` can flap around thresholds when machines clocks differ by even 1–2 seconds.

| `elapsed` since last successful poll | Status |
|---|---|
| ≤ `POLL_INTERVAL × 3` | 🟢 Online |
| ≤ `HEARTBEAT_TTL` | 🟡 Degraded |
| > `HEARTBEAT_TTL` | 🔴 Offline |

## Peer Discovery

mDNS multicast is not guaranteed to reach every robot pair on WiFi networks (AP isolation, multicast filtering, different broadcast domains). To handle this, robots also do **peer exchange**:

Every poll cycle, if a peer responds to `/state`, the robot also fetches that peer's `/peer_urls`. Any robots the peer knows about that are not yet known locally are added automatically. This means if robot1 knows about both robot2 and stretch, robot2 will discover stretch (and vice versa) within one poll cycle — without needing to add them to `STATIC_PEERS` or relying on mDNS.

---

## Architecture Overview

```
┌─────────────────────────────────┐
│  TurtleBot4 / Stretch           │
│                                 │
│  server.py  ─── ROS2 topics     │
│      │          (odom, battery, │
│      ▼           dock/runstop)  │
│  state.py ◄──────────────────   │
│      │                          │
│  client.py ── polls peers ──────┼──► other robots' /state
│      │      ── mDNS discovery   │
│      ▼                          │
│  dashboard.py (Rich live UI)    │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│  monitor/ (any device)          │
│                                 │
│  client.py ── polls all robots ─┼──► all robots' /state
│      │      ── mDNS discovery   │
│      ▼                          │
│  dashboard.py (Rich live table) │
└─────────────────────────────────┘
```

---

## File Responsibilities

| File | Role |
|---|---|
| `config.py` | All deployment-specific settings — **only file you need to edit** |
| `state.py` | Shared mutable state (own_state, peers, peer_urls, log buffer) |
| `server.py` | ROS2 subscriptions + FastAPI HTTP endpoints + mDNS advertisement |
| `client.py` | mDNS discovery + concurrent peer polling + heartbeat ticker |
| `dashboard.py` | Rich terminal UI — panels, tables, formatters |
| `main.py` | Wires all modules together, handles shutdown |


