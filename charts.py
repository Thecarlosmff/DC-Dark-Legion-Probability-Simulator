"""Pie charts for a DrawState. They return Figures (no plt.show) for st.pyplot."""
from __future__ import annotations

import numpy as np
from matplotlib.figure import Figure

import engine as eng
import simulation as sim

CHART_KINDS = ("Mythic", "Legendary", "Epic", "Overall")

# Overall chart: label -> color (reds = Mythic, golds = Legendary, purples = Epic)
OVERALL_COLORS = {
    "Mythic - Banner": "#7f0000",
    "Mythic - Other Champions": "#d32f2f",
    "Mythic - Legacy Pieces": "#ef9a9a",
    "Legendary - Champions": "#f9a825",
    "Legendary - Legacy Pieces": "#ffe082",
    "Epic - Champions": "#7b1fa2",
    "Epic - Legacy Pieces": "#ce93d8",
}


def _pie(ax, data: dict[str, int], title: str, colors: list[str] | None = None) -> None:
    data = {k: v for k, v in data.items() if v > 0}
    ax.set_title(title)
    if not data:
        ax.text(0.5, 0.5, "No data yet", ha="center", va="center")
        ax.set_axis_off()
        return
    wedges, *_ = ax.pie(
        list(data.values()),
        colors=colors,
        startangle=140,
        autopct=lambda p: f"{p:.1f}%" if p >= 4 else "",
        wedgeprops=dict(edgecolor="white", linewidth=0.8),
        textprops={"fontsize": 8},
    )
    ax.legend(
        wedges,
        [f"{k} ({v})" for k, v in data.items()],
        loc="center left",
        bbox_to_anchor=(1, 0.5),
        fontsize=8,
        frameon=False,
    )


def build_chart(state: eng.DrawState, kind: str) -> Figure:
    results = state.results
    fig = Figure(figsize=(7, 4.5), facecolor="white")
    ax = fig.subplots()

    def count(rarity, k):
        return sum(c for n, c in results.items() if state.item_info(n) == (rarity, k))

    if kind == "Mythic":
        data: dict[str, int] = {state.banner: results.get(state.banner, 0)}
        for name in state.boosted:
            data[name] = results.get(name, 0)
        other = sum(
            c for n, c in results.items()
            if n in state.pools.mythic_champions and n not in state.boosted
        )
        data["Other Champions"] = other
        data["Legacy Pieces"] = count(eng.MYTHIC, eng.LEGACY)
        _pie(ax, data, "Mythic Breakdown")

    elif kind in ("Legendary", "Epic"):
        p = state.pools
        champs, legacy = (
            (p.legendary_champions, p.legendary_legacy)
            if kind == "Legendary" else (p.epic_champions, p.epic_legacy)
        )
        data = {n: results.get(n, 0) for n in (*champs, *legacy)}
        _pie(ax, data, f"{kind} Breakdown")

    elif kind == "Overall":
        banner = results.get(state.banner, 0)
        data = {
            "Mythic - Banner": banner,
            "Mythic - Other Champions": count(eng.MYTHIC, eng.CHAMPION) - banner,
            "Mythic - Legacy Pieces": count(eng.MYTHIC, eng.LEGACY),
            "Legendary - Champions": count(eng.LEGENDARY, eng.CHAMPION),
            "Legendary - Legacy Pieces": count(eng.LEGENDARY, eng.LEGACY),
            "Epic - Champions": count(eng.EPIC, eng.CHAMPION),
            "Epic - Legacy Pieces": count(eng.EPIC, eng.LEGACY),
        }
        data = {k: v for k, v in data.items() if v > 0}
        _pie(ax, data, "Overall Rarity Composition", [OVERALL_COLORS[k] for k in data])

    else:
        raise ValueError(f"Unknown chart kind: {kind!r}")

    fig.tight_layout()
    return fig


def shard_histogram(result: sim.SimulationResult, owned: int = 0) -> Figure:
    """Distribution of banner champion shards after the simulated pulls."""
    total = result.banner_shards + owned
    lo, hi = int(total.min()), int(total.max())
    fig = Figure(figsize=(9, 4.2), facecolor="white")
    ax = fig.subplots()
    bins = min(60, hi - lo + 1)
    ax.hist(total, bins=bins, weights=np.full(len(total), 100 / len(total)),
            color="#d32f2f", edgecolor="white", linewidth=0.4)
    mean = float(total.mean())
    ax.axvline(mean, color="black", linewidth=1.5, label=f"Average: {mean:.1f}")
    top = ax.get_ylim()[1]
    for needed, name in sim.STAR_LEVELS:
        if lo <= needed <= hi:
            ax.axvline(needed, color="#555", linestyle=":", linewidth=0.8)
            ax.text(needed, top, f" {name}", rotation=90, va="top", ha="right", fontsize=7)
    ax.set_xlabel("Banner champion shards" + (" (including owned)" if owned else ""))
    ax.set_ylabel("% of simulations")
    ax.set_title(f"Shard distribution: {result.sessions:,} simulations x {result.pulls:,} pulls")
    ax.legend(loc="upper right", frameon=False, bbox_to_anchor=(1, 0.88))
    fig.tight_layout()
    return fig


def pulls_cdf(goal: sim.GoalResult, labels: dict[int, str]) -> Figure:
    """Chance of reaching each shard goal as a function of pulls."""
    fig = Figure(figsize=(9, 4.2), facecolor="white")
    ax = fig.subplots()
    qs = np.linspace(0, 1, 400)
    for target in goal.targets:
        x = np.quantile(goal.pulls[target], qs)
        ax.plot(x, qs * 100, linewidth=2, label=labels.get(target, f"{target} shards"))
    for level in (50, 90, 95):
        ax.axhline(level, color="#888", linestyle=":", linewidth=0.8)
        ax.text(ax.get_xlim()[0], level, f" {level}%", va="bottom", fontsize=7, color="#555")
    ax.set_xlabel("Pulls")
    ax.set_ylabel("Chance of reaching the goal (%)")
    ax.set_ylim(0, 101)
    ax.set_title(f"Pulls needed ({goal.sessions:,} simulations)")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(loc="lower right", frameon=False)
    fig.tight_layout()
    return fig


def bucket_chart(rows: list[dict], title: str) -> Figure:
    """Share of simulations ending at each star level (bar colors = star colors)."""
    fig = Figure(figsize=(9, 4.2), facecolor="white")
    ax = fig.subplots()
    if not rows:
        ax.text(0.5, 0.5, "No data", ha="center", va="center")
        ax.set_axis_off()
        return fig
    labels = [sim.level_stars(r["level"]) for r in rows]
    values = [r["chance"] * 100 for r in rows]
    bars = ax.bar(range(len(rows)), values, color=[sim.level_color(r["level"]) for r in rows],
                  edgecolor="black")
    ax.bar_label(bars, labels=[f"{v:.1f}%" if v >= 0.5 else "" for v in values], fontsize=7)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_ylabel("Percentage (%)")
    ax.set_title(title)
    fig.tight_layout()
    return fig


def count_chart(values: np.ndarray, color: str, title: str, xlabel: str) -> Figure:
    """Frequency (%) of each exact value."""
    fig = Figure(figsize=(9, 4.2), facecolor="white")
    ax = fig.subplots()
    unique, counts = np.unique(values, return_counts=True)
    ax.bar(unique, counts / counts.sum() * 100, color=color, edgecolor="black",
           width=0.9 if len(unique) > 1 else 0.5)
    if len(unique) <= 40:
        ax.set_xticks(unique)
        ax.tick_params(axis="x", rotation=90)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Frequency (%)")
    ax.set_title(title)
    fig.tight_layout()
    return fig
