# Code Reference

Detailed description of every module, class, function, and significant variable across all three robot folders. The `tb4/` and `stretch/` folders are structurally identical — differences are called out explicitly.

---

## `config.py`

The only file that should be edited between deployments. All other modules import from it; none ever write to it.

### Variables

| Variable | Type | Description |
|---|---|---|
| `ROBOT_ID` | `str` | Unique robot name. Used as the mDNS service name, in API responses, in log messages, and as the key in other robots' `peer_urls` dicts. Must be unique across the fleet. |
| `NAMESPACE` | `str` | ROS2 namespace prepended to all topic subscriptions. Set to `""` to subscribe on global topics (Stretch default). TB4 default is `"/robot1"` etc. |
| `PORT` | `int` | TCP port uvicorn binds to. All robots in the fleet can share the same port number since they run on different machines. |
| `POLL_INTERVAL` | `float` | Seconds between peer poll cycles. Lower values give more responsive status at the cost of more network traffic. Default `0.5`. |
| `HEARTBEAT_TTL` | `float` | Seconds of missed polls before a peer is removed from `state.peers`. Default `5.0`. With `POLL_INTERVAL=0.5` this allows 10 consecutive misses before removal. |
| `STATIC_PEERS` | `dict[str, str]` | `{ robot_id: "http://ip:port" }`. Populated into `state.peer_urls` at process start before mDNS or peer exchange runs. |

**Stretch-only:**

| Variable | Type | Description |
|---|---|---|
| `BATTERY_V_MIN` | `float` | Voltage at 0% charge. Default `11.8` V for a 12V lead-acid pack. |
| `BATTERY_V_MAX` | `float` | Voltage at 100% charge. Default `12.7` V. |

**Monitor-only:** Uses `STATIC_ROBOTS` (same format as `STATIC_PEERS`) instead.

---

## `state.py`

Shared mutable state for the process. Every other module imports this. Nothing outside `state.py` should define new shared state.

### `own_state: dict`

Written by `server.py` (ROS callbacks), read by the FastAPI endpoints and the dashboard. Never locked — written exclusively by the ROS spin thread, read by other threads (safe under the GIL for dict field access).

| Key | Written by | Description |
|---|---|---|
| `robot_id` | startup | From `config.ROBOT_ID`. Never changes. |
| `namespace` | startup | From `config.NAMESPACE`. Never changes. |
| `x`, `y` | `_odom_cb` | Position in metres from odometry origin. |
| `heading` | `_odom_cb` | Yaw in radians, computed from quaternion. |
| `is_docked` | `_dock_cb` | **TB4 only.** `True`/`False`/`None`. |
| `battery_percentage` | `_battery_cb` | 0–100. TB4: scaled from `BatteryState.percentage`. Stretch: estimated from voltage. |
| `battery_voltage` | `_battery_cb` | **Stretch only.** Raw volts. |
| `battery_current` | `_battery_cb` | **Stretch only.** Raw amps; positive = discharging. |
| `is_runstopped` | `_runstop_cb` | **Stretch only.** `True` if e-stop engaged. |
| `joint_state` | `_joint_states_cb` | **Stretch only.** Raw latest `JointState` arrays (`name`, `position`, `velocity`, `effort`) and `header_stamp`. |
| `joints` | `_joint_states_cb` | **Stretch only.** Keyed per-joint lookup map with `position`, `velocity`, `effort`. |
| `last_updated` | all callbacks | ISO 8601 UTC string, updated on every ROS callback. |
| `heartbeat_ts` | `_heartbeat_ticker` | Unix float (`time.time()`). Updated every `POLL_INTERVAL` regardless of ROS activity. Used as a process-alive indicator. |

### `peers: dict[str, dict]`

```python
{
    "robot2": {
        "state":     { ...same schema as own_state... },
        "last_seen": 12345.678,   # time.monotonic() of last successful poll
    }
}
```

Written by `client.poll_peers()`, read by API endpoints and the dashboard. Guarded by `peers_lock`.

### `peer_urls: dict[str, str]`

```python
{ "robot2": "http://10.0.0.2:8000" }
```

Populated from `config.STATIC_PEERS` at startup, then extended by mDNS discovery and peer exchange. Guarded by `peer_urls_lock`. Never shrinks during normal operation — URL eviction is not implemented (robot removal is handled by the `peers` dict TTL instead).

### `log(msg: str)`

Appends a timestamped Rich-markup string to the ring buffer. Use this anywhere in the codebase to surface a message in the dashboard log panel. Example:

```python
state.log("[cyan][mDNS][/cyan] Discovered [bold]robot2[/bold] at http://10.0.0.2:8000")
state.log("[red][error][/red] Something went wrong")
```

### `get_log_lines() -> list[str]`

Returns a snapshot copy of the log ring buffer. Used by `dashboard.build_log_panel()`. Copying inside the lock avoids holding the lock during rendering.

---

## `server.py`

### FastAPI endpoints

All endpoints are synchronous FastAPI route functions. They run in uvicorn's thread pool.

| Endpoint | Returns | Notes |
|---|---|---|
| `GET /state` | `own_state` dict | The complete state of this robot. |
| `GET /peers` | `{ robot_id: state_dict }` | All peers this robot is currently polling. The monitor can use this to get an indirect view of the fleet. |
| `GET /peers/{robot_id}` | single peer's state dict, or `{"error": "..."}` | Convenience endpoint for querying a specific peer. |
| `GET /peer_urls` | `{ robot_id: url }` | This robot's known peer URLs. Fetched by other robots during peer exchange to discover robots they don't yet know about. |
| `GET /heartbeat` | `{ robot_id, alive, timestamp }` | Lightweight liveness check. Not called by the polling loop (redundant since `/state` already returns `heartbeat_ts`), but useful for external tooling or debugging. |

### `class RobotStateListener(Node)`

A `rclpy.node.Node` subclass. Created once in `start_ros2()` and spun in a daemon thread.

**`__init__`** — Calls `super().__init__("comms_server", namespace=config.NAMESPACE or None)` then creates subscriptions for `odom`, `battery`, `is_runstopped`, and (Stretch only) `joint_states`. The node name `"comms_server"` is what appears in `ros2 node list`. Passing `namespace` here makes all relative topic names resolve under it — e.g. `"odom"` becomes `/robot1/odom` when `NAMESPACE="/robot1"`.

**`_odom_cb(msg: Odometry)`** — Extracts `x`, `y` from `msg.pose.pose.position` and computes yaw from the quaternion using the standard formula:

```
yaw = atan2(2(wz + xy), 1 - 2(y² + z²))
```

**`_dock_cb(msg: DockStatus)`** — TB4 only. Reads `msg.is_docked` directly.

**`_battery_cb(msg: BatteryState)`** — TB4: `msg.percentage` is 0.0–1.0, multiplied by 100. Stretch: `msg.percentage` is NaN; `battery_percentage` is estimated from `msg.voltage` using `estimate_battery_pct()`.

**`_joint_states_cb(msg: JointState)`** - Stretch only. Copies raw `name`, `position`, `velocity`, and `effort` arrays into `own_state["joint_state"]`, converts `msg.header.stamp` to ISO UTC as `header_stamp` when non-zero, and also builds `own_state["joints"]` keyed by joint name for direct lookup. Missing velocity/effort entries are stored as `None`.

**`_runstop_cb(msg: Bool)`** - Stretch only. Reads `msg.data`.

### `advertise_self() -> Zeroconf`

Registers this robot as a Zeroconf service of type `_robot._tcp.local.`. The service name is `{ROBOT_ID}._robot._tcp.local.`. The `properties` dict `{"robot_id": ROBOT_ID}` is what other robots read in their `_RobotListener.add_service()` to identify who is advertising. Returns the `Zeroconf` instance so `main.py` can unregister it on shutdown.

### `start_api_server() -> uvicorn.Server`

Creates a `uvicorn.Config` bound to `0.0.0.0:PORT` and starts `uvicorn.Server.run()` in a daemon thread. Returns the `Server` instance so `main.py` can set `server.should_exit = True` during shutdown. Log level is set to `WARNING` to suppress access logs that would bleed into the Rich dashboard.

### `start_ros2() -> RobotStateListener`

Calls `rclpy.init()`, creates `RobotStateListener`, and starts `rclpy.spin()` in a daemon thread. **`rclpy.init()` must be called before `rclpy.spin()` and must only be called once per process.** Returns the node, though `main.py` stores it mostly to prevent garbage collection.

---

## `client.py`

### `class _RobotListener(ServiceListener)`

The zeroconf mDNS listener. Three methods are required by the `ServiceListener` interface:

- **`add_service`** — Called when a new `_robot._tcp.local.` service appears. Decodes `robot_id` from the service's `properties` dict, constructs the URL, and adds it to `state.peer_urls` if not already present. Guards against adding itself by checking `robot_id != config.ROBOT_ID`.
- **`remove_service`** — Intentionally ignored. Peer removal is handled by the TTL in `poll_peers`.
- **`update_service`** — Delegates to `add_service` to handle IP changes.

### `start_discovery() -> Zeroconf`

Creates a `Zeroconf` instance and a `ServiceBrowser` watching `_robot._tcp.local.`. The browser runs in its own thread managed by zeroconf internally. Returns the `Zeroconf` instance for cleanup.

### `_heartbeat_ticker()`

A simple infinite loop that sets `state.own_state["heartbeat_ts"] = time.time()` every `POLL_INTERVAL` seconds. Runs in a daemon thread. Its purpose is to serve as a process-alive signal that is independent of ROS — if ROS topics go silent, `heartbeat_ts` keeps ticking, and `last_updated` does not.

### `_poll_one(robot_id, url) -> (robot_id, state_data | None, peer_urls | {})`

Performs two sequential HTTP GET requests to a single peer:
1. `GET /state` — the peer's own state. If this fails, returns `(robot_id, None, {})` immediately.
2. `GET /peer_urls` — the peer's known peer URLs, only attempted if `/state` succeeded.

Both calls use a `timeout=1.0` second. The function is designed to be submitted to a `ThreadPoolExecutor` — it blocks but runs concurrently with other peers.

### `poll_peers()`

The main polling loop. Uses a persistent `ThreadPoolExecutor(max_workers=16)` (not recreated each cycle) to dispatch `_poll_one` calls for all currently-known peers concurrently. `as_completed()` processes results as they arrive rather than waiting for all to finish.

After collecting results it processes peer exchange first (adds newly-discovered peer URLs to `state.peer_urls`), then updates `state.peers`. Sleeps `POLL_INTERVAL` at the end of each cycle. The effective cycle time is approximately `max(individual_peer_latency) + POLL_INTERVAL`, not `sum(all_peer_latencies)`.

### `start_heartbeat_ticker()` / `start_polling()`

Thin wrapper functions that launch the respective functions in daemon threads. Used by `main.py` to start background work in a consistent way.

---

## `dashboard.py`

Pure display logic — reads from `state.py`, never writes to it. All functions return Rich renderables and have no side effects.

### Formatting helpers

| Function | Purpose |
|---|---|
| `fmt_float(val, decimals)` | Formats a float to N decimal places, or `N/A` for `None`. |
| `fmt_bool(val, true_str, false_str, ...)` | Renders a bool as a coloured string. TB4 uses default green/yellow. Stretch passes `true_color="red"` for `is_runstopped` (danger = True). |
| `fmt_battery(pct, voltage)` | Colour-codes battery percentage (green ≥60%, yellow ≥25%, red <25%) with optional voltage suffix. |
| `fmt_timestamp(ts)` | Converts an ISO 8601 timestamp to a human-readable `"X.Xs ago"` string, colour-coded by age (green <2s, yellow <10s, red ≥10s). |

### `_peer_status(elapsed) -> str`

Converts `elapsed` (seconds since last successful poll) to a coloured status string. See [architecture.md](architecture.md) for the threshold logic.

### `build_own_panel() -> Panel`

Reads `state.own_state` and renders a Rich `Table` inside a green `Panel`. TB4 shows `is_docked`; Stretch shows `battery_voltage`, `battery_current`, and `is_runstopped`.

### `build_peers_table() -> Table`

Iterates the union of `state.peer_urls.keys()` and `state.peers.keys()` (so robots that are known but have never responded still appear as "Pending"). For each peer with a live entry, calls `_peer_status(elapsed)` and all the `fmt_*` helpers.

### `build_log_panel() -> Panel`

Calls `state.get_log_lines()` and renders the lines as a `Text` object inside a `Panel`. Lines may contain Rich markup (e.g. `[cyan]`, `[bold]`).

### `build_layout() -> Layout`

Assembles the full terminal layout: a `Layout` split vertically into a 2:1 ratio. The top half is split horizontally 1:3 between the own panel and the peers table. The bottom third is the log panel.

### `run_dashboard()`

The blocking entry point called by `main.py`. Creates a `Console`, wraps the layout in a `Live` context with `refresh_per_second=2`, and loops calling `live.update(build_layout())` every 0.5 seconds. Exits on `KeyboardInterrupt`.

---

## `main.py`

The wiring module. Calls the start functions from the other modules in order, then blocks on the dashboard. On `KeyboardInterrupt` or any exit, the `finally` block shuts down all subsystems.

### `_port_in_use(port) -> bool`

Attempts a `connect_ex` to `127.0.0.1:port`. If it succeeds (returns `0`), something is already listening. Used to detect a running `server.py` before attempting to start and print a clear error instead of a cryptic port-binding failure.

### Shutdown sequence (in `finally`)

```python
api_server.should_exit = True          # signals uvicorn to stop accepting requests
zc_advert.unregister_all_services()    # removes this robot from mDNS
zc_advert.close()
zc_discovery.close()
rclpy.shutdown()                       # signals rclpy.spin() to exit
```

All threads are daemon threads so they die automatically when the main thread exits, but explicit shutdown ensures clean deregistration from mDNS and ROS.

---

## `monitor/` differences

The monitor's modules follow the same structure but with these differences:

| Module | Difference |
|---|---|
| `state.py` | Has `robots` + `robots_lock` (equivalent to `peers`) and `robot_urls` + `robot_urls_lock`. No `own_state` or log buffer. |
| `client.py` | Has no `_heartbeat_ticker`. `_RobotListener` does not filter out `config.ROBOT_ID` (there is no "self" robot). Uses `robot_urls` / `robots` naming. |
| `dashboard.py` | Single flat table (no own-robot panel, no log panel). Includes a `Namespace` column. The `Docked` column gracefully shows `N/A` for Stretch robots that don't publish `is_docked`. |
| `server.py` | **Does not exist.** The monitor has no HTTP server and does not advertise via mDNS. |
| `main.py` | No port-in-use check (no server to conflict with). No ROS2 initialisation. |


