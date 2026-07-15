"""Pure matplotlib (Agg) renderer for the `!gate verlauf` history chart.

The Agg backend is selected BEFORE importing pyplot so the render never needs
a display. `render_gate_history_chart` takes the `list_gate_entries` output and
returns PNG bytes; it never touches Discord.
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from datetime import datetime
from io import BytesIO

from n3x_bot.gates import GATE_NAMES, _DROP_LABELS

_CHART_DROP_ITEMS = {"d": ["laser"], "e": ["lf4"], "z": ["havoc"],
                     "k": ["hercules", "lf4u"]}


def render_gate_history_chart(gate_type: str, entries: list[dict],
                              now: datetime, von=None, bis=None) -> bytes:
    fig, ax = plt.subplots()
    try:
        ax.set_title(f"Gate-Verlauf: {GATE_NAMES[gate_type]}")
        ax.set_xlabel("Datum")
        ax.set_ylabel("Kosten")

        if not entries:
            ax.text(0.5, 0.5, "keine Daten", ha="center", va="center",
                    transform=ax.transAxes)
        else:
            dates = [e["created_at"] for e in entries]
            costs = [e["cost"] for e in entries]
            ax.plot(dates, costs, marker="o", label="Kosten")
            ax.axhline(sum(costs) / len(costs), color="gray",
                       linestyle="--", label="Ø Kosten")
            if gate_type in _CHART_DROP_ITEMS:
                for item in _CHART_DROP_ITEMS[gate_type]:
                    hit_dates = [e["created_at"] for e in entries
                                 if e["drops"].get(item)]
                    hit_costs = [e["cost"] for e in entries
                                 if e["drops"].get(item)]
                    ax.scatter(hit_dates, hit_costs, label=_DROP_LABELS[item])
            ax.legend()

        buf = BytesIO()
        fig.savefig(buf, format="png")
        return buf.getvalue()
    finally:
        plt.close(fig)
