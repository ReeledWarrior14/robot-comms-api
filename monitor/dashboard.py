# =============================================================================
#  dashboard.py  —  Monitor Rich terminal dashboard
#
#  Displays a live table of all discovered robots with their state.
#  Handles mixed TB4 / Stretch fleets gracefully — TB4 robots show is_docked,
#  Stretch robots show battery_voltage alongside %; missing fields show N/A.
# =============================================================================

import time
from datetime import datetime, timezone

from rich.console import Console
from rich.live import Live
from rich.table import Table

import config
import state


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_float(val, decimals: int = 3) -> str:
    if val is None:
        return "[dim]N/A[/dim]"
    return f"{val:.{decimals}f}"


def fmt_bool(val, true_str: str, false_str: str) -> str:
    if val is None:
        return "[dim]N/A[/dim]"
    return f"[green]{true_str}[/green]" if val else f"[yellow]{false_str}[/yellow]"


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


def _robot_status(reachable: bool, elapsed: float) -> str:
    """
    Derive Online/Degraded/Offline from time since last successful poll.
    Uses the local monotonic clock only — avoids cross-machine clock skew
    that occurs when comparing remote heartbeat_ts against local time.time().
    """
    if reachable:
        if elapsed <= config.POLL_INTERVAL * 3:
            return "[green]\u25cf Online[/green]"
        elif elapsed <= config.HEARTBEAT_TTL:
            return "[yellow]\u25cf Degraded[/yellow]"
        return "[red]\u25cf Offline[/red]"
    elif elapsed <= config.HEARTBEAT_TTL:
        return "[yellow]● Degraded[/yellow]"
    return "[red]● Offline[/red]"


# ── Table builder ─────────────────────────────────────────────────────────────

def build_table() -> Table:
    now = time.monotonic()
    table = Table(
        title=(
            f"[bold cyan]Robot Monitor[/bold cyan]  "
            f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]"
        ),
        expand=True,
        border_style="bright_black",
    )
    table.add_column("Robot ID",    style="bold white",  min_width=10)
    table.add_column("Status",      min_width=12)
    table.add_column("Namespace",   style="magenta",     min_width=10)
    table.add_column("URL",         style="dim",         min_width=22)
    table.add_column("X",           min_width=8)
    table.add_column("Y",           min_width=8)
    table.add_column("Heading",     min_width=8)
    table.add_column("Docked",      min_width=8)
    table.add_column("Battery",     min_width=12)
    table.add_column("Last Update", min_width=12)

    with state.robots_lock:
        snapshot = dict(state.robots)
    with state.robot_urls_lock:
        all_ids = set(state.robot_urls.keys()) | set(snapshot.keys())

    for robot_id in sorted(all_ids):
        entry = snapshot.get(robot_id)
        if entry is None:
            url = state.robot_urls.get(robot_id, "?")
            table.add_row(
                robot_id, "[dim]Pending…[/dim]", "[dim]—[/dim]", url,
                *["[dim]—[/dim]"] * 6,
            )
            continue

        s         = entry.get("state", {})
        reachable = entry.get("reachable", False)
        last_seen = entry.get("last_seen", 0)
        url       = entry.get("url", "?")
        elapsed   = now - last_seen

        table.add_row(
            robot_id,
            _robot_status(reachable, elapsed),
            s.get("namespace", "[dim]—[/dim]"),
            url,
            fmt_float(s.get("x")),
            fmt_float(s.get("y")),
            fmt_float(s.get("heading")),
            fmt_bool(s.get("is_docked"), "Docked", "Free"),
            fmt_battery(s.get("battery_percentage"), s.get("battery_voltage")),
            fmt_timestamp(s.get("last_updated")),
        )

    if not all_ids:
        table.add_row("[dim]No robots found yet…[/dim]", *[""] * 9)

    return table


# ── Entry point ───────────────────────────────────────────────────────────────

def run_dashboard():
    """Block on the live dashboard until KeyboardInterrupt."""
    import time as _t
    console = Console()
    with Live(build_table(), console=console, refresh_per_second=2) as live:
        while True:
            _t.sleep(0.5)
            live.update(build_table())
