# Guides

---

## Running `server.py` on Robot Startup with systemd

`server.py` is designed to run headlessly — no terminal, no dashboard, just the ROS2 subscriber and HTTP API. The cleanest way to start it automatically is a `systemd` user or system service.

### Why not a system service?

ROS2 requires a sourced environment (`/opt/ros/<distro>/setup.bash`) and often a workspace overlay (`~/ws/install/setup.bash`). These are shell scripts that set `PATH`, `LD_LIBRARY_PATH`, `AMENT_PREFIX_PATH`, etc. A system service running as root won't have these unless you explicitly source them in the `ExecStart` command via a wrapper script.

The cleanest approach is a **wrapper script** that sources the environment first, then runs the Python file.

---

### Step 1 — Create the wrapper script

Create this file on the robot at e.g. `/home/<user>/start-robot-comms.sh`:

```bash
#!/bin/bash
# start-robot-comms.sh
# Sources the ROS2 environment and starts the comms server.

set -e

# Source ROS2
source /opt/ros/humble/setup.bash         # adjust distro: jazzy, iron, etc.

# Source your workspace overlay if you have one
# source /home/<user>/ament_ws/install/setup.bash

# Change to the robot folder (so Python finds config.py, state.py, etc.)
cd /home/<user>/ambulante-lab/comms_test/tb4   # or stretch/

# Run the server
exec python3 server.py
```

Make it executable:
```bash
chmod +x /home/<user>/start-robot-comms.sh
```

Test it manually first:
```bash
/home/<user>/start-robot-comms.sh
# Should print: [server] robot1 running — API on port 8000. Ctrl-C to stop.
```

---

### Step 2 — Create the systemd service file

Create `/etc/systemd/system/robot-comms.service` (requires sudo):

```ini
[Unit]
Description=Robot comms server (ROS2 + HTTP API)
# Start after the network is up and ROS2 middleware is available.
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<user>                          # replace with your username, e.g. ubuntu
Group=<user>
ExecStart=/home/<user>/start-robot-comms.sh

# Restart automatically if it crashes.
Restart=on-failure
RestartSec=5s

# Give it time to cleanly shut down (mDNS deregistration etc.)
TimeoutStopSec=10s

# Forward stdout/stderr to journald
StandardOutput=journal
StandardError=journal
SyslogIdentifier=robot-comms

[Install]
WantedBy=multi-user.target
```

---

### Step 3 — Enable and start the service

```bash
# Reload systemd so it picks up the new file
sudo systemctl daemon-reload

# Enable: start automatically on every boot
sudo systemctl enable robot-comms

# Start it now without rebooting
sudo systemctl start robot-comms

# Verify it's running
sudo systemctl status robot-comms
```

---

### Viewing logs

```bash
# Last 50 lines
sudo journalctl -u robot-comms -n 50

# Follow live (like tail -f)
sudo journalctl -u robot-comms -f

# Since last boot only
sudo journalctl -u robot-comms -b
```

---

### Stopping the service to run `main.py`

```bash
sudo systemctl stop robot-comms
cd /home/<user>/ambulante-lab/comms_test/tb4
source /opt/ros/humble/setup.bash
python3 main.py

# When done, restart the headless service:
sudo systemctl start robot-comms
```

---

### ROS2 domain ID (multi-robot on the same network)

If multiple robots are on the same network and using the same ROS2 DDS domain, their topics may interfere. Each robot should use a unique `ROS_DOMAIN_ID`:

```bash
# In start-robot-comms.sh, after sourcing:
export ROS_DOMAIN_ID=1    # robot1 uses 1, robot2 uses 2, etc.
```

The comms system communicates via HTTP (not ROS2), so domain ID only affects which ROS2 topics each robot subscribes to — it does not affect peer discovery or the HTTP API.

---

---

## Testing Without Physical Robots

You can fully exercise the monitor, dashboard, peer discovery, and peer exchange on a single laptop by running multiple instances of `server.py` on different ports.

### Simulated fleet with fake state injection

The trick is that `own_state` is just a Python dict. You can populate it with fake values over HTTP by adding a temporary test endpoint, or more simply by editing `state.py` directly with a small script.

---

### Step 1 — Create fake robot instances

Each instance needs its own folder with a different `config.py`. The quickest way is to copy one of the existing folders:

```bash
cd /home/anayn/ambulante-lab/comms_test

cp -r tb4/ fake_robot1/
cp -r tb4/ fake_robot2/
cp -r stretch/ fake_stretch/
```

Edit `fake_robot1/config.py`:
```python
ROBOT_ID  = "robot1"
NAMESPACE = "/robot1"
PORT      = 8001
STATIC_PEERS = {
    "robot2":   "http://127.0.0.1:8002",
    "stretch1": "http://127.0.0.1:8003",
}
```

Edit `fake_robot2/config.py`:
```python
ROBOT_ID  = "robot2"
NAMESPACE = "/robot2"
PORT      = 8002
STATIC_PEERS = {
    "robot1": "http://127.0.0.1:8001",
}
```

Edit `fake_stretch/config.py`:
```python
ROBOT_ID  = "stretch1"
NAMESPACE = ""
PORT      = 8003
STATIC_PEERS = {
    "robot1": "http://127.0.0.1:8001",
}
```

---

### Step 2 — Add a state injection endpoint

Add this to `server.py` in each fake folder (or add it to the original and it won't hurt — it just won't be needed on real robots):

```python
# server.py — testing only
from fastapi import Body

@app.post("/inject")
def inject_state(data: dict = Body(...)):
    """Merge provided fields into own_state. For testing only."""
    state.own_state.update(data)
    return {"ok": True}
```

---

### Step 3 — Run instances without ROS2

The server will crash on import because `rclpy` and the ROS2 message types are imported at the top of `server.py`. Create a minimal `server_norос.py` inside each fake folder (or use environment variable gating):

The simplest approach — create a standalone fake server script in each folder:

```python
# fake_server.py  — drop this in fake_robot1/, fake_robot2/, fake_stretch/
import threading, time, uvicorn
from fastapi import FastAPI, Body
from datetime import datetime, timezone

import config

own_state = {
    "robot_id":           config.ROBOT_ID,
    "namespace":          config.NAMESPACE or "(none)",
    "x":                  None, "y": None, "heading": None,
    "battery_percentage": None,
    "is_docked":          None,   # TB4 fields
    "last_updated":       None,
    "heartbeat_ts":       None,
}

peer_urls = dict(config.STATIC_PEERS)

app = FastAPI()

@app.get("/state")
def get_state(): return own_state

@app.get("/peers")
def get_peers(): return {}

@app.get("/peer_urls")
def get_peer_urls(): return dict(peer_urls)

@app.get("/heartbeat")
def heartbeat():
    return {"robot_id": config.ROBOT_ID, "alive": True,
            "timestamp": datetime.now(timezone.utc).isoformat()}

@app.post("/inject")
def inject(data: dict = Body(...)):
    own_state.update(data)
    return {"ok": True}

def _ticker():
    while True:
        own_state["heartbeat_ts"] = time.time()
        time.sleep(config.POLL_INTERVAL)

threading.Thread(target=_ticker, daemon=True).start()

if __name__ == "__main__":
    print(f"[fake] {config.ROBOT_ID} on port {config.PORT}")
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, log_level="warning")
```

---

### Step 4 — Start the fake fleet

Open a terminal per instance (or use `&` backgrounding):

```bash
# Terminal 1
cd fake_robot1/ && python3 fake_server.py

# Terminal 2
cd fake_robot2/ && python3 fake_server.py

# Terminal 3
cd fake_stretch/ && python3 fake_server.py
```

---

### Step 5 — Inject fake state

```bash
# Give robot1 a position and battery
curl -s -X POST http://127.0.0.1:8001/inject \
  -H "Content-Type: application/json" \
  -d '{"x": 1.5, "y": 0.3, "heading": 0.78, "battery_percentage": 85.0,
       "is_docked": false, "last_updated": "2026-03-08T10:00:00+00:00"}'

# Give robot2 a different position
curl -s -X POST http://127.0.0.1:8002/inject \
  -H "Content-Type: application/json" \
  -d '{"x": -2.1, "y": 1.0, "heading": -1.57, "battery_percentage": 41.0,
       "is_docked": false, "last_updated": "2026-03-08T10:00:00+00:00"}'

# Give stretch a position with stretch-specific fields
curl -s -X POST http://127.0.0.1:8003/inject \
  -H "Content-Type: application/json" \
  -d '{"x": 0.0, "y": -1.0, "heading": 3.14, "battery_percentage": 62.0,
       "battery_voltage": 12.3, "battery_current": -0.5,
       "is_runstopped": false, "last_updated": "2026-03-08T10:00:00+00:00"}'
```

---

### Step 6 — Run the monitor against the fake fleet

Edit `monitor/config.py` to point at the local fake instances:
```python
STATIC_ROBOTS = {
    "robot1":   "http://127.0.0.1:8001",
    "robot2":   "http://127.0.0.1:8002",
    "stretch1": "http://127.0.0.1:8003",
}
```

Then:
```bash
cd monitor/
python3 main.py
```

You should see all three robots Online with the injected state.

---

### Testing peer exchange

To verify peer exchange works without full mDNS, configure the fake robots so only robot1 knows about both others, but robot2 and stretch don't know about each other:

```python
# fake_robot2/config.py  — only knows robot1
STATIC_PEERS = { "robot1": "http://127.0.0.1:8001" }

# fake_stretch/config.py  — only knows robot1
STATIC_PEERS = { "robot1": "http://127.0.0.1:8001" }
```

After one poll cycle, robot2 should query robot1's `/peer_urls`, discover stretch1, and add it. Verify:
```bash
# Wait a second, then:
curl http://127.0.0.1:8002/peer_urls
# Should now contain stretch1 even though it wasn't in config.STATIC_PEERS
```

---

### Testing liveness and TTL

Simulate a robot going offline by stopping one of the fake server processes (`Ctrl-C` in its terminal). The other instances and the monitor should show it as Degraded within `POLL_INTERVAL × 3` seconds, then Offline after `HEARTBEAT_TTL` seconds.
