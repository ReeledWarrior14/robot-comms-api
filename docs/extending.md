# Extending the System

Step-by-step guides for the most common modifications.

---

## Adding a New ROS Topic to the State

This is the most common change. All edits are in `server.py` and `state.py` for the relevant robot folder.

**Example: Subscribe to `/cmd_vel` and expose the current linear speed.**

### Step 1 — Add the field to `state.py`

```python
# state.py
own_state: dict = {
    ...
    "linear_speed": None,   # m/s from cmd_vel
}
```

### Step 2 — Import the message type in `server.py`

```python
# server.py
from geometry_msgs.msg import Twist
```

### Step 3 — Add the subscription in `RobotStateListener.__init__`

```python
self.create_subscription(Twist, "cmd_vel", self._cmd_vel_cb, 10)
```

### Step 4 — Write the callback

```python
def _cmd_vel_cb(self, msg: Twist):
    state.own_state["linear_speed"] = round(msg.linear.x, 3)
    state.own_state["last_updated"] = now_iso()
```

That's it. The new field will now appear automatically in `GET /state` responses and in any peer's view of this robot (via `/state` polling).

### Step 5 — Show it in the dashboard (optional)

In `dashboard.py`, add a row to `build_own_panel()`:

```python
tbl.add_row("Linear Speed", fmt_float(s.get("linear_speed"), 3) + " m/s"
            if s.get("linear_speed") is not None else "[dim]N/A[/dim]")
```

And/or add a column to `build_peers_table()`:

```python
tbl.add_column("Speed", min_width=8)

# inside the row loop:
fmt_float(s.get("linear_speed"), 3),
```

> **Note:** Adding a column to the peers table means the column will appear for all peers, including those of a different robot type that don't publish the field. They will show `N/A` automatically because `fmt_float(None)` returns `"[dim]N/A[/dim]"`.

---

## Adding a New API Endpoint

All endpoints are FastAPI route functions in `server.py`. Add them between the existing `@app.get(...)` blocks.

**Example: A `/status` endpoint that returns a human-readable summary.**

```python
# server.py
@app.get("/status")
def get_status():
    s = state.own_state
    docked = "docked" if s.get("is_docked") else "free"
    battery = f"{s.get('battery_percentage', '?')}%"
    return {
        "robot_id": s["robot_id"],
        "summary": f"{s['robot_id']} is {docked}, battery {battery}",
    }
```

**Example: A POST endpoint to set a mission label.**

```python
# server.py  (add to imports: from fastapi import Body)
_mission_label: str = ""

@app.post("/mission")
def set_mission(label: str = Body(..., embed=True)):
    global _mission_label
    _mission_label = label
    state.log(f"[cyan][api][/cyan] Mission set to: [bold]{label}[/bold]")
    return {"ok": True, "mission": _mission_label}

@app.get("/mission")
def get_mission():
    return {"mission": _mission_label}
```

Endpoints are available immediately when the server starts — no restart needed during development if you add them before launch.

---

## Adding a New Robot Type

To support a new robot (e.g. a drone, an AMR from another vendor), create a new folder by copying the most similar existing one and modifying the three files that are robot-specific: `server.py`, `state.py`, and `dashboard.py`. `client.py`, `config.py`, and `main.py` require minimal or no changes.

### Step 1 — Copy the closest folder

```bash
cp -r tb4/ myrobot/
cd myrobot/
```

### Step 2 — Edit `config.py`

Set `ROBOT_ID`, `NAMESPACE`, and `PORT`. Add `STATIC_PEERS` if needed. Remove any hardware-specific constants that don't apply.

### Step 3 — Edit `state.py`

Replace the robot-type-specific fields in `own_state` with the fields relevant to the new robot. Keep all shared fields (`robot_id`, `namespace`, `x`, `y`, `heading`, `last_updated`, `heartbeat_ts`):

```python
own_state: dict = {
    "robot_id":           config.ROBOT_ID,
    "namespace":          config.NAMESPACE or "(none)",
    "x":                  None,
    "y":                  None,
    "heading":            None,
    # ── New robot-specific fields ──
    "altitude":           None,    # metres above takeoff point
    "is_armed":           None,    # True if motors are armed
    "battery_percentage": None,
    "last_updated":       None,
    "heartbeat_ts":       None,
}
```

### Step 4 — Edit `server.py`

Replace the imports with the correct message types, and rewrite `RobotStateListener` with the correct topic names and callbacks:

```python
from sensor_msgs.msg import BatteryState, NavSatFix
from std_msgs.msg import Bool, Float32

class RobotStateListener(Node):
    def __init__(self):
        super().__init__("comms_server", namespace=config.NAMESPACE or None)
        self.create_subscription(Odometry,    "odom",       self._odom_cb,    10)
        self.create_subscription(BatteryState,"battery",    self._battery_cb, 10)
        self.create_subscription(Bool,        "is_armed",   self._armed_cb,   10)
        self.create_subscription(Float32,     "altitude",   self._altitude_cb,10)

    def _altitude_cb(self, msg: Float32):
        state.own_state["altitude"]      = round(msg.data, 2)
        state.own_state["last_updated"]  = now_iso()

    def _armed_cb(self, msg: Bool):
        state.own_state["is_armed"]      = msg.data
        state.own_state["last_updated"]  = now_iso()

    # keep _odom_cb and _battery_cb from the original
```

Everything else in `server.py` (`advertise_self`, `start_api_server`, `start_ros2`, the FastAPI endpoints) does not need to change.

### Step 5 — Edit `dashboard.py`

Update `build_own_panel()` to show the new fields, and update `build_peers_table()` columns if you want them visible in the peer view.

### Step 6 — Update the monitor (optional)

If the new robot exposes fields you want to see in `monitor/dashboard.py`, add columns to `build_table()`. Existing columns (`x`, `y`, `battery_percentage`) will already work since those field names are shared.

---

## Changing Poll Rate or Liveness Thresholds

Edit `POLL_INTERVAL` and `HEARTBEAT_TTL` in `config.py`. The liveness thresholds in `dashboard.py` and `monitor/dashboard.py` derive from `config.POLL_INTERVAL` and `config.HEARTBEAT_TTL` at runtime — you do not need to edit the dashboard code.

| Goal | Change |
|---|---|
| Faster status updates | Lower `POLL_INTERVAL` (min practical: ~0.2s) |
| More tolerance for brief network drops | Raise `HEARTBEAT_TTL` |
| Longer "Degraded" window before Offline | Raise `HEARTBEAT_TTL` relative to `POLL_INTERVAL` |
| Smaller "Online" window (stricter) | Lower `POLL_INTERVAL * 3` by lowering `POLL_INTERVAL` |

---

## Adding a Persistent Log (File Output)

The current log buffer is in-memory only (`deque(maxlen=12)`). To also write to a file, modify `state.log()`:

```python
# state.py
import re

_LOG_FILE = "/tmp/robot-comms.log"   # or configure via config.py

def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[dim]{ts}[/dim] {msg}"
    with _log_lock:
        _log_buffer.append(entry)
    # Strip Rich markup for the file
    plain = re.sub(r"\[.*?\]", "", entry)
    with open(_LOG_FILE, "a") as f:
        f.write(plain + "\n")
```

---

## Exposing State Over a Different Protocol

The HTTP API in `server.py` is the only outbound interface. To add a different transport (e.g. MQTT, WebSocket, UDP broadcast), add it as another thread in `main.py`:

```python
# main.py  (example: broadcast own_state as JSON over UDP every second)
import json, socket as _socket, threading

def _udp_broadcaster():
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_BROADCAST, 1)
    while True:
        payload = json.dumps(state.own_state).encode()
        sock.sendto(payload, ("<broadcast>", 9999))
        time.sleep(1.0)

threading.Thread(target=_udp_broadcaster, daemon=True).start()
```

This pattern works for anything that can run in a daemon thread and read from `state.own_state`.

---

## Running Multiple Robots on the Same Machine

Each instance needs a different `PORT` and a different `ROBOT_ID`. Since modules are loaded from the current directory by Python, the cleanest approach is to keep each robot's config isolated in its own folder and run from that folder:

```bash
# Terminal 1
cd tb4/
ROBOT_ID override is in config.py — already isolated per folder
python3 server.py

# Terminal 2  (a second TB4 instance simulated locally)
cp -r tb4/ tb4_robot2/
# Edit tb4_robot2/config.py: ROBOT_ID="robot2", PORT=8001
cd tb4_robot2/
python3 server.py
```

Because Python resolves `import config` relative to the script's directory (when run directly), each folder's `config.py` is used independently.
