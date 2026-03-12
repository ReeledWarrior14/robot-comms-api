# =============================================================================
#  dashboard.py  —  TurtleBot4 Rich terminal dashboard
#
#  Builds the three-panel live layout:
#    Left:   own robot state panel
#    Right:  peers table
#    Bottom: rolling log
# =============================================================================

import time
from datetime import datetime, timezone

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import config
import state


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_float(val, decimals: int = 3) -> str:
    if val is None:
        return "[dim]N/A[/dim]"
    return f"{val:.{decimals}f}"


def fmt_bool(val, true_str: str, false_str: str,
             true_color: str = "green", false_color: str = "yellow") -> str:
    if val is None:
        return "[dim]N/A[/dim]"
    color = true_color if val else false_color
    label = true_str  if val else false_str
    return f"[{color}]{label}[/{color}]"


def fmt_battery(pct, voltage=None) -> str:
    if pct is None and voltage is None:
        return "[dim]N/A[/dim]"
    color = "green" if (pct or 0) >= 60 else ("yellow" if (pct or 0) >= 25 else "red")
    pct_str = f"{pct:.1f}%" if pct is not None else "?%"
    v_str   = f" [dim]{voltage:.2f}V[/dim]" if voltage is not None else ""
    return f"[{color}]{pct_str}[/{color}]{v_str}"


def fmt_timestamp(ts: str | None) -> str:
    if not ts:
        return "[dim]N/A[/dim]"
    try:
        dt  = datetime.fromisoformat(ts)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        color = "green" if age < 2 else ("yellow" if age < 10 else "red")
        return f"[{color}]{age:.1f}s ago[/{color}]"
    except Exception:
        return ts


def _peer_status(elapsed: float) -> str:
    """
    Derive Online/Degraded/Offline from time since last successful poll.
    Uses the local monotonic clock (elapsed = now - last_seen) which is
    reliable across machines, unlike comparing heartbeat_ts timestamps
    from a remote clock against local time.time().
    """
    if elapsed <= config.POLL_INTERVAL * 3:
        return "[green]● Online[/green]"
    elif elapsed <= config.HEARTBEAT_TTL:
        return "[yellow]● Degraded[/yellow]"
    return "[red]● Offline[/red]"


# ── Panel / table builders ────────────────────────────────────────────────────

def build_own_panel() -> Panel:
    s = state.own_state
    tbl = Table(box=None, show_header=False, padding=(0, 1))
    tbl.add_column(style="dim",        min_width=18)
    tbl.add_column(style="bold white")
    tbl.add_row("Robot ID",    f"[bold cyan]{s['robot_id']}[/bold cyan]")
    tbl.add_row("Namespace",   f"[magenta]{s['namespace']}[/magenta]")
    tbl.add_row("API Port",    str(config.PORT))
    tbl.add_row("X",           fmt_float(s.get("x")))
    tbl.add_row("Y",           fmt_float(s.get("y")))
    tbl.add_row("Heading",     fmt_float(s.get("heading")))
    tbl.add_row("Docked",      fmt_bool(s.get("is_docked"), "Docked", "Free"))
    tbl.add_row("Battery",     fmt_battery(s.get("battery_percentage")))
    tbl.add_row("Last ROS Callback", fmt_timestamp(s.get("last_updated")))
    tbl.add_row("Last API Query", fmt_timestamp(s.get("last_api_query")))
    hb = s.get("heartbeat_ts")
    tbl.add_row("Heartbeat TS", f"[green]{hb:.3f}[/green]" if hb else "[dim]N/A[/dim]")
    return Panel(tbl, title="[bold green]● This Robot (TB4)[/bold green]", border_style="green")


def build_peers_table() -> Table:
    now = time.monotonic()
    tbl = Table(expand=True, border_style="bright_black")
    tbl.add_column("Robot ID",    style="bold white", min_width=10)
    tbl.add_column("Status",      min_width=10)
    tbl.add_column("URL",         style="dim",        min_width=22)
    tbl.add_column("X",           min_width=8)
    tbl.add_column("Y",           min_width=8)
    tbl.add_column("Heading",     min_width=8)
    tbl.add_column("Docked",      min_width=8)
    tbl.add_column("Battery",     min_width=9)
    tbl.add_column("Last ROS Callback", min_width=17)

    with state.peer_urls_lock:
        all_ids = set(state.peer_urls.keys())
    with state.peers_lock:
        snapshot = dict(state.peers)
    all_ids |= set(snapshot.keys())

    for robot_id in sorted(all_ids):
        url   = state.peer_urls.get(robot_id, "?")
        entry = snapshot.get(robot_id)
        if entry is None:
            tbl.add_row(robot_id, "[dim]Pending…[/dim]", url, *["[dim]—[/dim]"] * 6)
            continue
        s       = entry.get("state", {})
        elapsed = now - entry.get("last_seen", 0)
        tbl.add_row(
            robot_id, _peer_status(elapsed), url,
            fmt_float(s.get("x")), fmt_float(s.get("y")), fmt_float(s.get("heading")),
            fmt_bool(s.get("is_docked"), "Docked", "Free"),
            fmt_battery(s.get("battery_percentage"), s.get("battery_voltage")),
            fmt_timestamp(s.get("last_updated")),
        )

    if not all_ids:
        tbl.add_row("[dim]No peers discovered yet…[/dim]", *[""] * 8)

    return tbl


def build_log_panel() -> Panel:
    lines = state.get_log_lines()
    text = Text.from_markup("\n".join(lines) if lines else "[dim]No events yet.[/dim]")
    return Panel(text, title="[bold]Log[/bold]", border_style="bright_black")


def build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="top", ratio=2),
        Layout(name="log", ratio=1),
    )
    layout["top"].split_row(
        Layout(build_own_panel(), name="self",  ratio=1),
        Layout(
            Panel(build_peers_table(), title="[bold cyan]Peers[/bold cyan]", border_style="cyan"),
            name="peers", ratio=3,
        ),
    )
    layout["log"].update(build_log_panel())
    return layout


# ── Entry point ───────────────────────────────────────────────────────────────

def run_dashboard():
    """Block on the live dashboard until KeyboardInterrupt."""
    console = Console()
    with Live(build_layout(), console=console, refresh_per_second=2) as live:
        while True:
            import time as _t
            _t.sleep(0.5)
            live.update(build_layout())
