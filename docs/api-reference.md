# API Reference

Every robot running `server.py` or `main.py` exposes an HTTP API on its configured `PORT` (default `8000`). The API is served by [FastAPI](https://fastapi.dev/) via uvicorn. All responses are JSON.

**Base URL:** `http://<robot-ip>:<PORT>`

An interactive API explorer (Swagger UI) is available at `http://<robot-ip>:<PORT>/docs` whenever the server is running.

---

## Endpoints

### `GET /state`

Returns the complete current state of this robot.

**Response — TurtleBot4**

```jsonc
{
  "robot_id":           "robot1",
  "namespace":          "/robot1",
  "x":                  1.234,        // metres; null if odometry not yet received
  "y":                  -0.567,       // metres; null if odometry not yet received
  "heading":            0.312,        // radians; null if odometry not yet received
  "is_docked":          false,        // bool; null if dock_status not yet received
  "battery_percentage": 87.3,         // 0–100; null if battery_state not yet received
  "last_updated":       "2026-03-08T14:23:01.456789+00:00",  // ISO 8601 UTC; null until first callback
  "heartbeat_ts":       1741441381.2  // Unix float; null until heartbeat ticker starts
}
```

**Response — Stretch Hello Robot 3**

```jsonc
{
  "robot_id":           "stretch1",
  "namespace":          "(none)",
  "x":                  0.0,
  "y":                  0.0,
  "heading":            -1.571,
  "battery_voltage":    12.45,        // volts; null until battery topic received
  "battery_current":    -1.23,        // amps; negative = charging
  "battery_percentage": 72.2,         // estimated from voltage (see config.BATTERY_V_MIN/MAX)
  "is_runstopped":      false,        // true if e-stop / runstop engaged
  "last_updated":       "2026-03-08T14:23:01.456789+00:00",
  "heartbeat_ts":       1741441381.2
}
```

**Field reference**

| Field | Type | All robots | Notes |
|---|---|---|---|
| `robot_id` | string | ✓ | Matches `config.ROBOT_ID`. Never changes at runtime. |
| `namespace` | string | ✓ | ROS2 namespace in use, or `"(none)"` if blank. |
| `x` | float \| null | ✓ | Odometry X position, metres. |
| `y` | float \| null | ✓ | Odometry Y position, metres. |
| `heading` | float \| null | ✓ | Yaw in radians (−π to π). Derived from odometry quaternion. |
| `battery_percentage` | float \| null | ✓ | 0–100. Method differs by robot type (see below). |
| `last_updated` | ISO 8601 string \| null | ✓ | UTC timestamp of the most recent ROS callback. Useful for detecting stale state. |
| `heartbeat_ts` | float \| null | ✓ | `time.time()` on the robot machine. Ticked every `POLL_INTERVAL`s independent of ROS. Use to confirm the process is alive. Do **not** compare across machines due to clock skew — use network round-trip time instead. |
| `is_docked` | bool \| null | TB4 only | `true` if on the dock. |
| `battery_voltage` | float \| null | Stretch only | Raw voltage in volts. |
| `battery_current` | float \| null | Stretch only | Raw current in amps. Positive = discharging, negative = charging. |
| `is_runstopped` | bool \| null | Stretch only | `true` when e-stop or runstop button is engaged (motors disabled). |

**Battery percentage methods by robot type:**
- **TurtleBot4:** `BatteryState.percentage × 100` (reported directly by the hardware).
- **Stretch 3:** Estimated linearly from voltage: `(V − V_min) / (V_max − V_min) × 100`, clamped 0–100. Defaults: `V_min = 11.8 V`, `V_max = 12.7 V`. Adjustable in `config.py`.

**null values** indicate the relevant ROS topic has not yet published since the server started. Consumers should treat `null` as "unknown" rather than zero or false.

---

### `GET /peers`

Returns the last known state of all peers this robot is currently polling, keyed by `robot_id`.

**Response**

```jsonc
{
  "robot2": {
    "robot_id":           "robot2",
    "namespace":          "/robot2",
    "x":                  3.1,
    "y":                  0.4,
    "heading":            1.57,
    "is_docked":          false,
    "battery_percentage": 54.0,
    "last_updated":       "2026-03-08T14:23:00.900000+00:00",
    "heartbeat_ts":       1741441380.9
  },
  "stretch1": {
    "robot_id":           "stretch1",
    ...
  }
}
```

Returns an empty object `{}` if no peers have been discovered or polled yet.

**Notes:**
- Only peers that have responded to at least one poll are included. Robots in `STATIC_PEERS` that have never responded are absent.
- The state data is a snapshot from the last successful `/state` poll. It may be up to `POLL_INTERVAL` seconds old.
- If a peer exceeds `HEARTBEAT_TTL` without responding, it is removed from this dict entirely.

---

### `GET /peers/{robot_id}`

Returns the last known state of a single peer.

**Path parameter:** `robot_id` — the `robot_id` string of the target peer (e.g. `robot2`, `stretch1`).

**Response — found**

The peer's full state object (same schema as a single entry in `GET /peers`).

**Response — not found**

```json
{ "error": "robot2 not found" }
```

HTTP status is `200` in both cases (FastAPI default). Check for the presence of the `"error"` key to distinguish.

---

### `GET /peer_urls`

Returns the set of peer URLs this robot currently knows about, keyed by `robot_id`. This is used internally by the peer exchange mechanism but is also useful for debugging discovery.

**Response**

```jsonc
{
  "robot2":   "http://10.40.108.86:8000",
  "stretch1": "http://10.40.98.25:8000"
}
```

Returns an empty object `{}` if no peers have been discovered yet.

**Notes:**
- Includes robots discovered via mDNS, static config, and peer exchange.
- Includes robots that have been discovered but never successfully polled (unlike `GET /peers`).
- URLs are never removed from this dict at runtime — removal only happens on process restart.

---

### `GET /heartbeat`

Lightweight liveness check. Returns immediately without accessing any shared state.

**Response**

```json
{
  "robot_id":  "robot1",
  "alive":     true,
  "timestamp": "2026-03-08T14:23:01.456789+00:00"
}
```

| Field | Type | Description |
|---|---|---|
| `robot_id` | string | This robot's ID. |
| `alive` | bool | Always `true` — if the server is dead, the request will fail rather than return `false`. |
| `timestamp` | ISO 8601 string | UTC time on the robot at the moment of the request. |

**Notes:**
- The polling loop does **not** call this endpoint — liveness is derived from the `heartbeat_ts` field in `/state`.
- Useful for external tooling, health checks, or quick manual checks: `curl http://10.40.118.220:8000/heartbeat`

---

## Error Behaviour

The API does not return non-200 HTTP status codes under normal operating conditions. All documented error responses (e.g. peer not found) return HTTP `200` with a JSON body containing an `"error"` key.

If the server is unreachable (robot offline, network issue, wrong IP/port), the HTTP connection will fail entirely — the client will receive a connection refused error or a timeout rather than an HTTP response.

FastAPI will return `422 Unprocessable Entity` for malformed path parameters, and `500 Internal Server Error` if an unhandled exception occurs in a route handler.

---

## Usage Examples

### Shell (`curl`)

```bash
# Get this robot's state
curl http://10.40.118.220:8000/state

# Get all peers
curl http://10.40.118.220:8000/peers

# Get a specific peer
curl http://10.40.118.220:8000/peers/stretch1

# Check what peers this robot knows about
curl http://10.40.118.220:8000/peer_urls

# Quick liveness check
curl http://10.40.118.220:8000/heartbeat
```

### Python (`requests`)

```python
import requests

BASE = "http://10.40.118.220:8000"

# Get own state
state = requests.get(f"{BASE}/state", timeout=1.0).json()
print(state["x"], state["y"], state["heading"])

# Check battery
if state["battery_percentage"] is not None and state["battery_percentage"] < 20:
    print("Low battery!")

# Get all peers
peers = requests.get(f"{BASE}/peers", timeout=1.0).json()
for robot_id, peer_state in peers.items():
    print(f"{robot_id}: x={peer_state['x']}, y={peer_state['y']}")

# Poll a specific peer
resp = requests.get(f"{BASE}/peers/robot2", timeout=1.0).json()
if "error" not in resp:
    print(f"robot2 is at ({resp['x']}, {resp['y']})")

# Check if a robot is reachable
try:
    r = requests.get(f"{BASE}/heartbeat", timeout=0.5)
    print("Online" if r.ok else "Error")
except requests.exceptions.RequestException:
    print("Unreachable")
```

### Python (async, `httpx`)

```python
import httpx
import asyncio

async def get_all_states(robot_urls: dict[str, str]) -> dict[str, dict]:
    """Fetch /state from multiple robots concurrently."""
    async with httpx.AsyncClient(timeout=1.0) as client:
        tasks = {
            robot_id: client.get(f"{url}/state")
            for robot_id, url in robot_urls.items()
        }
        results = {}
        for robot_id, coro in tasks.items():
            try:
                resp = await coro
                if resp.status_code == 200:
                    results[robot_id] = resp.json()
            except httpx.RequestError:
                pass
        return results

robot_urls = {
    "robot1":  "http://10.40.118.220:8000",
    "robot2":  "http://10.40.108.86:8000",
    "stretch1":"http://10.40.98.25:8000",
}
states = asyncio.run(get_all_states(robot_urls))
```

---

## Determining Liveness from the API

The `/heartbeat` endpoint and the `heartbeat_ts` field in `/state` serve different use cases:

| | `GET /heartbeat` | `heartbeat_ts` in `GET /state` |
|---|---|---|
| What it tells you | The HTTP server is up | The Python process is alive with its background threads running |
| Cost | 1 extra HTTP request | Free — already in `/state` |
| Cross-machine clock safe | N/A (just check reachability) | **No** — do not compute `time.time() - heartbeat_ts` across machines with different clocks |
| Recommended for | Manual checks, external healthchecks | Diagnostic display on the local robot's own dashboard only |

**To determine if a remote robot is alive from your own code**, the recommended approach is to track whether `/state` requests succeed and how recently:

```python
import time

last_seen: float = 0.0   # time.monotonic()

try:
    resp = requests.get(f"{url}/state", timeout=1.0)
    if resp.status_code == 200:
        last_seen = time.monotonic()
except Exception:
    pass

elapsed = time.monotonic() - last_seen
if elapsed < 1.5:    # POLL_INTERVAL * 3
    status = "Online"
elif elapsed < 5.0:  # HEARTBEAT_TTL
    status = "Degraded"
else:
    status = "Offline"
```

This is exactly what the built-in polling loop does internally.
