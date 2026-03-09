# Robot Fleet API — Compact Reference

Base: `http://<robot-ip>:8000` (PORT configurable). All responses JSON. Swagger UI at `/docs`.

## Endpoints

### GET /state
Own robot state.
```json
{
  "robot_id": "robot1", "namespace": "/robot1",
  "x": 1.234, "y": -0.567, "heading": 0.312,
  "battery_percentage": 87.3,
  "last_updated": "2026-03-08T14:23:01.456789+00:00",
  "heartbeat_ts": 1741441381.2
}
```
TB4 adds: `"is_docked": false`
Stretch adds: `"battery_voltage": 12.45, "battery_current": -1.23, "is_runstopped": false`
All fields nullable (null = topic not yet received). `heartbeat_ts` = Unix float, process-alive ticker independent of ROS — do NOT compare across machines (clock skew).

### GET /peers
All currently-polled peers, keyed by robot_id. Empty `{}` if none. Only robots that have responded at least once appear. Removed after `HEARTBEAT_TTL` (default 5s) of no response.
```json
{ "robot2": { ...same schema as /state... }, "stretch1": { ... } }
```

### GET /peers/{robot_id}
Single peer state. HTTP 200 always; check for `"error"` key if not found.
```json
{ "error": "robot2 not found" }
```

### GET /peer_urls
All known peer URLs (from static config + mDNS + peer exchange). Includes undiscovered/unreachable robots. Never shrinks at runtime.
```json
{ "robot2": "http://10.40.108.86:8000", "stretch1": "http://10.40.98.25:8000" }
```

### GET /heartbeat
Liveness ping. Returns immediately.
```json
{ "robot_id": "robot1", "alive": true, "timestamp": "2026-03-08T14:23:01+00:00" }
```

## Field Reference

| Field | Type | Robots | Notes |
|---|---|---|---|
| `robot_id` | str | all | Unique name, never changes |
| `namespace` | str | all | ROS2 namespace or `"(none)"` |
| `x`, `y` | float\|null | all | Odometry position, metres |
| `heading` | float\|null | all | Yaw radians −π to π |
| `battery_percentage` | float\|null | all | 0–100. TB4: from hardware. Stretch: estimated from voltage |
| `last_updated` | ISO8601\|null | all | UTC, last ROS callback |
| `heartbeat_ts` | float\|null | all | `time.time()` on robot, do not diff cross-machine |
| `is_docked` | bool\|null | TB4 | True if on dock |
| `battery_voltage` | float\|null | Stretch | Volts raw |
| `battery_current` | float\|null | Stretch | Amps; positive=discharging |
| `is_runstopped` | bool\|null | Stretch | True if e-stop engaged |

## Liveness Pattern (recommended)
Track elapsed time since last successful poll using local monotonic clock. Do NOT use `heartbeat_ts` for cross-machine liveness.
```python
import time, requests
last_seen = 0.0
try:
    r = requests.get(f"{url}/state", timeout=1.0)
    if r.ok: last_seen = time.monotonic()
except: pass
elapsed = time.monotonic() - last_seen
# elapsed <= 1.5  → Online  (POLL_INTERVAL * 3)
# elapsed <= 5.0  → Degraded (HEARTBEAT_TTL)
# elapsed >  5.0  → Offline
```

## Quick Examples
```bash
curl http://10.40.118.220:8000/state
curl http://10.40.118.220:8000/peers
curl http://10.40.118.220:8000/peers/stretch1
curl http://10.40.118.220:8000/peer_urls
curl http://10.40.118.220:8000/heartbeat
```
```python
import requests
s = requests.get("http://10.40.118.220:8000/state", timeout=1.0).json()
peers = requests.get("http://10.40.118.220:8000/peers", timeout=1.0).json()
```

## Notes
- No auth. HTTP only. No non-200 status codes under normal operation (errors returned as JSON with `"error"` key).
- `/heartbeat` is not called by the internal polling loop — it exists for external tooling only.
- Concurrent multi-robot polling: use `ThreadPoolExecutor` or `asyncio`+`httpx`; robots respond independently.
