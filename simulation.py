"""
Fast Monte-Carlo simulation: many independent sessions run in parallel with
numpy (one array slot per session), so 10,000 sessions x 1,000 pulls takes
about a second instead of minutes. Nothing is stored per pull.

Two questions are answered:
    simulate()        how many shards do I get from N pulls?
    simulate_goals()  how many pulls do I need to reach X shards?

The rules are the same as engine.DrawState.draw(); tests compare the two.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

import engine as eng

# Shards given by each item (shards of the pulled champion itself)
BANNER_COPY_SHARDS = 40        # limited Mythic champion: 40 shards = unlocked
MYTHIC_CHAMPION_SHARDS = 10
LEGENDARY_CHAMPION_SHARDS = 10
EPIC_CHAMPION_SHARDS = 1

MAX_TOTAL_DRAWS = 500_000_000  # sessions x pulls accepted by the UI

# (total shards, level) for a champion
STAR_LEVELS: tuple[tuple[int, str], ...] = (
    (40, "Unlocked"),
    (42, "1 White Star"), (45, "2 White Stars"), (50, "3 White Stars"),
    (60, "4 White Stars"), (80, "5 White Stars"),
    (100, "1 Blue Star"), (120, "2 Blue Stars"), (140, "3 Blue Stars"),
    (170, "4 Blue Stars"), (200, "5 Blue Stars"),
    (240, "1 Purple Star"), (280, "2 Purple Stars"), (320, "3 Purple Stars"),
    (360, "4 Purple Stars"), (400, "5 Purple Stars"),
    (440, "1 Gold Star"), (480, "2 Gold Stars"), (520, "3 Gold Stars"),
    (580, "4 Gold Stars"), (640, "5 Gold Stars"),
    (720, "1 Red Star"), (800, "2 Red Stars"), (880, "3 Red Stars"),
    (960, "4 Red Stars"), (1040, "5 Red Stars"),
)

LEVEL_NAMES = {shards: name for shards, name in STAR_LEVELS}


@dataclass
class SimulationResult:
    """Per-session counts (arrays of length `sessions`)."""
    pulls: int
    banner_copies: np.ndarray        # limited Mythic champion copies
    extra_shards: np.ndarray         # bonus banner shards (non-limited Mythics)
    mythic_champions: np.ndarray     # non-banner Mythic champions
    mythic_legacy: np.ndarray
    legendary_champions: np.ndarray
    legendary_legacy: np.ndarray
    epic_champions: np.ndarray
    epic_legacy: np.ndarray
    forced_mythic: np.ndarray        # Mythics given by the 50-pull pity
    forced_banner: np.ndarray        # banner Mythics given by the 3-Mythic pity
    milestone_shards: int            # banner shards from milestones reached (same for all)
    milestones_reached: tuple[eng.Milestone, ...]

    @property
    def sessions(self) -> int:
        return len(self.banner_copies)

    @property
    def mythics(self) -> np.ndarray:
        """All Mythics pulled per session (banner copies + other Mythics)."""
        return self.banner_copies + self.mythic_champions + self.mythic_legacy

    @property
    def banner_shards(self) -> np.ndarray:
        """Banner champion shards gained per session."""
        return (BANNER_COPY_SHARDS * self.banner_copies.astype(np.int64)
                + self.extra_shards + self.milestone_shards)

    def chance_to_reach(self, owned: int = 0) -> list[tuple[str, int, float]]:
        """(level, total shards, probability 0-1) for levels above `owned`."""
        total = self.banner_shards + owned
        return [(name, needed, float((total >= needed).mean()))
                for needed, name in STAR_LEVELS if needed > owned]


class _Sessions:
    """`sessions` parallel drawing sessions; step() advances all by one pull."""

    def __init__(self, catalog: eng.Catalog, banner: str, params: eng.SessionParams,
                 sessions: int, seed: int | None):
        if banner not in catalog.banners:
            raise ValueError(f"Unknown banner: {banner!r}")
        if sessions < 1:
            raise ValueError("sessions must be at least 1")
        self.rules = catalog.rules
        self.rng = np.random.default_rng(seed)
        self.n = n = sessions

        def counter(start: int = 0) -> np.ndarray:
            return np.full(n, start, dtype=np.int32)

        self.mythic_pity = counter(params.mythic_pity)
        self.legendary_pity = counter(params.legendary_pity)
        self.since_banner = counter(params.non_banner_since_last)
        self.non_limited = counter(params.non_limited_pulled)
        self.banner_copies, self.extra = counter(), counter()
        self.myth_ch, self.myth_lg = counter(), counter()
        self.leg_ch, self.leg_lg = counter(), counter()
        self.epic_ch, self.epic_lg = counter(), counter()
        self.forced_m, self.forced_b = counter(), counter()

    def step(self) -> np.ndarray:
        """One pull for every session. Returns the banner shards it gave."""
        rng, n = self.rng, self.n
        self.mythic_pity += 1
        self.legendary_pity += 1
        roll = rng.random(n)

        forced = self.mythic_pity >= eng.MYTHIC_GUARANTEE
        mythic = forced | (roll < eng.MYTHIC_RATE)
        legendary = ~mythic & (
            (self.legendary_pity >= eng.LEGENDARY_GUARANTEE)
            | (roll < eng.MYTHIC_RATE + eng.LEGENDARY_RATE))
        epic = ~(mythic | legendary)

        # Legendary / Epic: 50% champion, 50% legacy piece
        champion = rng.random(n) < 0.5
        self.leg_ch += legendary & champion
        self.leg_lg += legendary & ~champion
        self.epic_ch += epic & champion
        self.epic_lg += epic & ~champion

        # Mythic: guaranteed banner after 3 non-limited Mythics, else 5% banner
        r = rng.random(n)
        forced_banner = mythic & (self.since_banner >= eng.BANNER_PITY_LIMIT)
        is_banner = forced_banner | (mythic & (r < eng.MYTHIC_BANNER_CHANCE))
        non_banner = mythic & ~is_banner

        self.forced_m += forced
        self.forced_b += forced_banner
        self.banner_copies += is_banner
        # (r < 0.55: boosted champion, r >= 0.775: other champion, between: legacy)
        is_legacy = non_banner & (r >= eng.MYTHIC_BANNER_CHANCE + eng.MYTHIC_BOOSTED_CHANCE) \
            & (r < eng.LEGACY_PIECE_THRESHOLD)
        self.myth_lg += is_legacy
        self.myth_ch += non_banner & ~is_legacy

        self.since_banner = np.where(is_banner, 0, self.since_banner + non_banner).astype(np.int32)
        self.non_limited += non_banner

        gain = BANNER_COPY_SHARDS * is_banner.astype(np.int32)
        if self.rules.extra_shards:
            gets = non_banner & (
                (self.non_limited <= eng.GUARANTEED_SHARD_PULLS)
                | (rng.random(n) < eng.LATE_SHARD_CHANCE))
            s = rng.random(n)
            amount = np.where(s < eng.SHARD_ODDS[0][1], eng.SHARD_ODDS[0][0],
                              np.where(s < eng.SHARD_ODDS[0][1] + eng.SHARD_ODDS[1][1],
                                       eng.SHARD_ODDS[1][0], eng.SHARD_ODDS[2][0]))
            extra_now = np.where(gets, amount, 0).astype(np.int32)
            self.extra += extra_now
            gain += extra_now

        # pity resets (Legendary resets Legendary pity; Mythic resets both)
        self.legendary_pity[legendary | mythic] = 0
        self.mythic_pity[mythic] = 0
        return gain


def simulate(
    catalog: eng.Catalog,
    banner: str,
    params: eng.SessionParams,
    pulls: int,
    sessions: int,
    seed: int | None = None,
    progress: Callable[[float], None] | None = None,
) -> SimulationResult:
    """Run `sessions` independent sessions of `pulls` pulls from `params`."""
    if pulls < 1:
        raise ValueError("pulls must be at least 1")
    s = _Sessions(catalog, banner, params, sessions, seed)

    step = max(1, pulls // 50)
    for t in range(pulls):
        s.step()
        if progress and (t + 1) % step == 0:
            progress((t + 1) / pulls)

    # Milestones depend only on the number of pulls, not on luck.
    done = params.pulls_on_banner
    reached = tuple(m for m in catalog.rules.milestones if done < m.pulls <= done + pulls)
    if progress:
        progress(1.0)
    return SimulationResult(
        pulls=pulls,
        banner_copies=s.banner_copies, extra_shards=s.extra,
        mythic_champions=s.myth_ch, mythic_legacy=s.myth_lg,
        legendary_champions=s.leg_ch, legendary_legacy=s.leg_lg,
        epic_champions=s.epic_ch, epic_legacy=s.epic_lg,
        forced_mythic=s.forced_m, forced_banner=s.forced_b,
        milestone_shards=sum(m.banner_shards for m in reached),
        milestones_reached=reached,
    )


# --------------------------------------------------------------------------
# Probability tables (levels, counts, summary)
# --------------------------------------------------------------------------
_LEVEL_COLORS = {"white": "#ffffff", "blue": "#4da6ff", "purple": "#a855f7",
                 "gold": "#facc15", "red": "#ff5555"}
_THRESHOLDS = np.array([shards for shards, _ in STAR_LEVELS])


def level_color(name: str) -> str:
    """Bar color of a level name such as '2 Blue Stars'."""
    low = name.lower()
    for key, color in _LEVEL_COLORS.items():
        if key in low:
            return color
    return "#a0a0a0" if "unlocked" in low else "#888888"


def level_stars(name: str) -> str:
    """'2 Blue Stars' -> 'Blue ★★'; 'Locked' / 'Unlocked' unchanged."""
    if name in ("Locked", "Unlocked"):
        return name
    count, color, *_ = name.split()
    return f"{color} {'★' * int(count)}"


def level_buckets(result: SimulationResult, owned: int = 0) -> list[dict]:
    """
    Where sessions end up: for each level reached (highest one), the share of
    sessions ending exactly there ("chance") and at that level or higher
    ("at_least"). Only levels that occurred are listed. `owned` is added first.
    """
    total = result.banner_shards + owned
    idx = np.searchsorted(_THRESHOLDS, total, side="right")   # 0 = Locked
    counts = np.bincount(idx, minlength=len(STAR_LEVELS) + 1)
    n = len(total)
    rows = []
    for i, c in enumerate(counts):
        if c:
            shards, name = (0, "Locked") if i == 0 else STAR_LEVELS[i - 1]
            rows.append({"shards": shards, "level": name, "count": int(c),
                         "chance": c / n, "at_least": counts[i:].sum() / n})
    return rows


def count_distribution(values: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(distinct values, chance of exactly that value, chance of at least it)."""
    unique, counts = np.unique(values, return_counts=True)
    n = counts.sum()
    at_least = (n - (np.cumsum(counts) - counts)) / n
    return unique, counts / n, at_least


def summary_stats(result: SimulationResult) -> dict[str, float]:
    """Statistics of the banner shards gained, plus Mythic rates."""
    gained = result.banner_shards.astype(float)
    pulls_total = result.pulls * result.sessions
    mythic_rate = float(result.mythics.sum()) / pulls_total
    limited_rate = float(result.banner_copies.sum()) / pulls_total
    return {
        "mean": float(gained.mean()), "median": float(np.median(gained)),
        "q1": float(np.percentile(gained, 25)), "q3": float(np.percentile(gained, 75)),
        "min": float(gained.min()), "max": float(gained.max()), "std": float(gained.std()),
        "mythic_rate": mythic_rate,
        "pulls_per_mythic": 1 / mythic_rate if mythic_rate else float("inf"),
        "limited_rate": limited_rate,
        "pulls_per_limited": 1 / limited_rate if limited_rate else float("inf"),
    }


# --------------------------------------------------------------------------
# Pulls needed to reach a shard goal
# --------------------------------------------------------------------------
@dataclass
class GoalResult:
    """Pulls needed per session to gain each target number of banner shards."""
    targets: tuple[int, ...]               # shards still needed (>= 1)
    pulls: dict[int, np.ndarray]           # target -> pulls per session
    capped: int = 0                        # sessions that hit the safety cap

    @property
    def sessions(self) -> int:
        return len(next(iter(self.pulls.values())))

    def stats(self, target: int) -> dict[str, float]:
        p = self.pulls[target]
        return {"mean": float(p.mean()), "median": float(np.median(p)),
                "min": int(p.min()), "max": int(p.max()), "std": float(p.std())}

    def pulls_for_chance(self, target: int, chance: float) -> int:
        """Smallest number of pulls that succeeds in at least `chance` of runs."""
        return int(np.ceil(np.quantile(self.pulls[target], chance, method="higher")))

    def chance_within(self, target: int, pulls: int) -> float:
        """Probability (0-1) of reaching the target in at most `pulls` pulls."""
        return float((self.pulls[target] <= pulls).mean())


def simulate_goals(
    catalog: eng.Catalog,
    banner: str,
    params: eng.SessionParams,
    targets: list[int] | tuple[int, ...],
    sessions: int,
    seed: int | None = None,
    progress: Callable[[float], None] | None = None,
    max_pulls: int = 200_000,
) -> GoalResult:
    """
    For every target (banner shards still needed) run `sessions` sessions,
    drawing until the target is reached, and record how many pulls it took.
    All targets are measured in the same runs.

    Shards counted: 40 per banner copy + extra shards + milestone banner_shards.
    """
    targets = tuple(sorted({int(t) for t in targets if int(t) >= 1}))
    if not targets:
        raise ValueError("at least one target of 1 or more shards is needed")
    s = _Sessions(catalog, banner, params, sessions, seed)

    bonus_at: dict[int, int] = {}   # banner pulls -> milestone shards
    for m in catalog.rules.milestones:
        if m.banner_shards:
            bonus_at[m.pulls] = bonus_at.get(m.pulls, 0) + m.banner_shards

    total = np.zeros(sessions, dtype=np.int64)
    pulls_needed = {t: np.zeros(sessions, dtype=np.int32) for t in targets}
    open_ = {t: np.ones(sessions, dtype=bool) for t in targets}
    top = targets[-1]
    t = 0
    while t < max_pulls:
        t += 1
        total += s.step()
        total += bonus_at.get(params.pulls_on_banner + t, 0)
        for target in targets:
            newly = open_[target] & (total >= target)
            pulls_needed[target][newly] = t
            open_[target] &= ~newly
        if progress and t % 25 == 0:
            progress(min(0.99, float((total >= top).mean())))
        if not open_[top].any():   # the highest target is the last one to finish
            break

    capped = int(open_[top].sum())
    for target in targets:
        pulls_needed[target][open_[target]] = max_pulls
    if progress:
        progress(1.0)
    return GoalResult(targets, pulls_needed, capped)
