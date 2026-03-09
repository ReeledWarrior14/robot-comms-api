# Troubleshooting

Symptoms, likely causes, and fixes for common problems.

---

## Robot shows "Pending…" in the dashboard — never changes to Online

The peer's URL is known (from static config, mDNS, or peer exchange) but every HTTP poll is failing.

**Check 1 — Is the robot's server running?**
```bash
# From any machine on the network:
curl http://<robot-ip>:8000/heartbeat
```
If this times out or refuses the connection, the server isn't running or isn't reachable. SSH to the robot and check:
```bash
ps aux | grep server.py
# or if running via systemd:
sudo systemctl status robot-comms
```

**Check 2 — Can you reach the robot at all?**
```bash
ping <robot-ip>
```
If ping fails, the issue is network-level — wrong IP, robot on a different subnet, or WiFi not associated.

**Check 3 — Is something else using the port?**
```bash
# On the robot:
sudo ss -tlnp | grep 8000
# or:
sudo lsof -i :8000
```
If another process owns the port, stop it or change `PORT` in `config.py`.

**Check 4 — Is the server bound to the right interface?**
The server binds to `0.0.0.0` (all interfaces) by default, so this is rarely the problem. Confirm with:
```bash
sudo ss -tlnp | grep 8000
# Should show: 0.0.0.0:8000
```

---

## All state fields are `null` — robot is Online but x/y/heading/battery never appear

The server is running and reachable, but no ROS callbacks are firing.

**Check 1 — Is ROS2 running?**
```bash
ros2 topic list
```
If this returns nothing or an error, ROS2 is not initialised. Make sure you sourced the environment before running the server:
```bash
source /opt/ros/humble/setup.bash
source ~/your_ws/install/setup.bash   # if applicable
```

**Check 2 — Are the topics publishing?**
```bash
ros2 topic echo /robot1/odom          # TB4 with NAMESPACE="/robot1"
ros2 topic echo /odom                 # Stretch with NAMESPACE=""
ros2 topic echo /battery              # Stretch battery topic
ros2 topic echo /is_runstopped        # Stretch runstop
```
If a topic has no output, the driver or hardware node is not running.

**Check 3 — Are you on the right namespace?**
```bash
ros2 topic list | grep odom
```
The output should match `<NAMESPACE>/odom` (e.g. `/robot1/odom`). If the topic is on a different namespace than what's in `config.NAMESPACE`, the subscription won't receive anything. Either fix `config.NAMESPACE` or redirect at launch:
```bash
python3 server.py --ros-args -r __ns:=/actual_namespace
```

**Check 4 — TB4 only: is `irobot_create_msgs` installed?**
```bash
ros2 interface show irobot_create_msgs/msg/DockStatus
```
If this errors, the package is missing:
```bash
sudo apt install ros-humble-irobot-create-msgs
```

**Check 5 — Are callbacks being dropped silently?**
Run the server in a terminal (not via systemd) and watch for any Python tracebacks. An import error for a message type will prevent the node from starting without a clear error in the Rich dashboard.

---

## `last_updated` is very old — odometry stopped updating

The server was receiving ROS messages but they stopped.

- Check if the robot's drive node crashed: `ros2 node list`
- Check the hardware — TB4 creates wheel odometry; if the motors are in error state odometry stops
- Stretch: check if the runstop is engaged (`is_runstopped: true`) — this does not stop the odometry topic, but is often the cause of a robot that's otherwise not moving

Note: `heartbeat_ts` will still be current even if `last_updated` is stale — they are updated by different threads. If `heartbeat_ts` is also stale, the Python process itself is frozen or dead.

---

## mDNS discovery not working — robots not finding each other automatically

**Most common cause on WiFi:** AP client isolation or multicast filtering. Many enterprise and lab WiFi networks block mDNS between clients. This is normal — use `STATIC_PEERS` in `config.py` as the primary discovery mechanism and rely on mDNS only as a bonus.

**Check — can mDNS packets reach the other robot?**
```bash
# On Linux, use avahi-browse as a quick test:
avahi-browse _robot._tcp
# Should list robots that are advertising. If nothing appears, mDNS is filtered.
```

**Check — is the zeroconf service registered?**
The log panel should show `[mDNS] Advertising robot1 at <ip>:8000` on startup. If it doesn't, there was a registration error — look for a Python traceback.

**Check — duplicate robot_id on the network?**
If two robots share the same `ROBOT_ID`, the second registration will fail or behave unpredictably. Every robot must have a unique `ROBOT_ID`. Check `config.py` on each machine.

---

## Peer exchange not propagating — robot2 and stretch still can't see each other

Peer exchange requires at least one robot to successfully poll another that knows about the missing peer. If robot1 knows both robot2 and stretch, robot2 should discover stretch within one poll cycle (≤ `POLL_INTERVAL` seconds).

**Verify robot1 knows both:**
```bash
curl http://<robot1-ip>:8000/peer_urls
# Should show both robot2 and stretch1
```

**Verify robot2 is polling robot1 at all:**
```bash
curl http://<robot1-ip>:8000/peers
# Should include robot2 with a recent last_updated
```

If robot2 does not appear in robot1's `/peers`, robot2 cannot reach robot1 — fix that connection first.

**Verify after one poll cycle:**
```bash
curl http://<robot2-ip>:8000/peer_urls
# After one successful poll of robot1, stretch1 should appear here
```

---

## Status flaps between Online and Degraded rapidly

Most likely clock skew between machines — the old `heartbeat_ts`-based status logic was susceptible to this. The current implementation uses `elapsed` (local monotonic clock) so this should not occur anymore.

If you're still seeing it with the current codebase:
- A network that loses nearly every other packet will cause genuine flapping — check packet loss with `ping -c 100 <robot-ip>` and look at the loss percentage
- `POLL_INTERVAL` that is too low for the network latency — if polls are taking >1s (the request timeout), effectively every poll fails: raise the timeout in `_poll_one` or raise `POLL_INTERVAL`
- WiFi congestion causing bursty latency spikes — try `HEARTBEAT_TTL = 10.0` to make the Degraded window wider

---

## `ERROR: port 8000 is already in use` when running `main.py`

`server.py` is already running on this machine. Stop it before running `main.py`:
```bash
sudo systemctl stop robot-comms       # if running via systemd
# or find and kill the process:
kill $(lsof -ti:8000)
```
See the README for the explanation of why they can't run simultaneously.

---

## mDNS address is wrong — robot advertises 127.0.0.1 instead of its real IP

`socket.gethostbyname(socket.gethostname())` can return `127.0.0.1` on some Linux configurations where `/etc/hosts` maps the hostname to loopback.

Fix in `server.py` > `advertise_self()`:
```python
# Replace:
ip = socket.gethostbyname(socket.gethostname())

# With (picks the first non-loopback IPv4 address):
import subprocess
ip = subprocess.check_output(
    ["hostname", "-I"], text=True
).split()[0]
```

Or hardcode the IP in `advertise_self()` if the robot has a static IP — and just use `STATIC_PEERS` in all other robots' configs rather than relying on mDNS at all.

---

## Rich dashboard is garbled or not rendering correctly

- Run in a terminal emulator that supports 256 colours and UTF-8 (most modern terminals do)
- If running over SSH, ensure your SSH client is passing `TERM` correctly: `ssh -o SendEnv=TERM user@robot`
- Set `TERM=xterm-256color` in the shell before running if the terminal looks wrong
- The dashboard requires the terminal to be at least ~100 columns wide; smaller terminals cause Rich to wrap columns unpredictably

---

## `rclpy.init()` error: `rcl_init() failed`

ROS2 environment is not sourced. Always source before running:
```bash
source /opt/ros/humble/setup.bash
python3 server.py
```

If using systemd, the service file must source the setup script explicitly — see [guides.md](guides.md).
