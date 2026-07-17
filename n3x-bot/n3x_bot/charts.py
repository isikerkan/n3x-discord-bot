"""Pure matplotlib (Agg) renderer for the `/gate verlauf` history chart.

The Agg backend is selected BEFORE importing pyplot so the render never needs
a display. `render_gate_history_chart` takes the `list_gate_entries` output and
returns PNG bytes; it never touches Discord.
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from datetime import datetime, time
from io import BytesIO

from n3x_bot.gates import GATE_NAMES, _DROP_LABELS

_CHART_DROP_ITEMS = {"d": ["laser"], "e": ["lf4"], "z": ["havoc"],
                     "k": ["hercules", "lf4u"]}

_CHART_CAPTION = "Befehl: /gate verlauf gate:<gate> [von] [bis]  ·  Gates: a b c d e z k"


def _to_local_naive(dt, tz):
    """Convert an (aware, UTC-stored) timestamp to LOCAL wall-clock, tz-stripped.

    gate_entries.created_at is stored in UTC. matplotlib formats aware
    datetimes in UTC by default, so plotting them directly showed times/dates
    1–2h off. Converting to the configured tz and dropping tzinfo makes
    matplotlib render the local wall-clock verbatim.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None and tz is not None:
        dt = dt.astimezone(tz)
    return dt.replace(tzinfo=None)


def render_gate_history_chart(gate_type: str, entries: list[dict],
                              now: datetime, von=None, bis=None) -> bytes:
    tz = getattr(now, "tzinfo", None)
    fig, ax = plt.subplots()
    try:
        ax.set_title(f"Gate-Verlauf: {GATE_NAMES[gate_type]}")
        ax.set_xlabel("Datum")
        ax.set_ylabel("Kosten")

        if not entries:
            ax.text(0.5, 0.5, "keine Daten", ha="center", va="center",
                    transform=ax.transAxes)
        else:
            dates = [_to_local_naive(e["created_at"], tz) for e in entries]
            costs = [e["cost"] for e in entries]
            ax.plot(dates, costs, marker="o", label="Kosten")
            ax.axhline(sum(costs) / len(costs), color="gray",
                       linestyle="--", label="Ø Kosten")
            if gate_type in _CHART_DROP_ITEMS:
                for item in _CHART_DROP_ITEMS[gate_type]:
                    hit_dates = [_to_local_naive(e["created_at"], tz)
                                 for e in entries if e["drops"].get(item)]
                    hit_costs = [e["cost"] for e in entries
                                 if e["drops"].get(item)]
                    ax.scatter(hit_dates, hit_costs, label=_DROP_LABELS[item])
            ax.legend()

        # Clamp the x-axis to the requested date window so a date-range query
        # shows that window, not just the extent of the returned data. Naive
        # (local) bounds to match the naive-local plotted timestamps. When
        # von/bis are None we leave matplotlib's auto-fit alone.
        if von is not None:
            ax.set_xlim(left=datetime.combine(von, time.min))
        if bis is not None:
            ax.set_xlim(right=datetime.combine(bis, time.max))

        fig.subplots_adjust(bottom=0.18)
        fig.text(0.5, 0.01, _CHART_CAPTION, ha="center", fontsize=8,
                 color="grey")

        buf = BytesIO()
        fig.savefig(buf, format="png")
        return buf.getvalue()
    finally:
        plt.close(fig)
