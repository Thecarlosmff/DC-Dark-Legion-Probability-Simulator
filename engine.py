"""
Gacha engine: pure draw logic, no UI and no module-level mutable state.

Everything that used to be a global in funcs.py (pity counters, results,
history, selected banner) lives in a DrawState instance. In Streamlit that
instance is stored in st.session_state, so every visitor gets their own.

Data lives in one folder per server profile: data/<profile>/
    pools.json    regular items per rarity: champions and legacy pieces
    banners.json  limited banner champions and their boosted Mythics
    rules.json    (optional) server rules: extra shards on/off, milestones
Edit those files, not the code.
"""
from __future__ import annotations

import difflib
import json
import random
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

# --------------------------------------------------------------------------
# Rates / rules
# --------------------------------------------------------------------------
MYTHIC_RATE = 0.03
LEGENDARY_RATE = 0.16
MYTHIC_GUARANTEE = 50          # forced Mythic on the 50th pull without one
LEGENDARY_GUARANTEE = 10       # forced Legendary+ on the 10th pull without one
MYTHIC_BANNER_CHANCE = 0.05    # chance a Mythic is the limited banner champion
MYTHIC_BOOSTED_CHANCE = 0.50   # chance a Mythic is an odds-boosted champion
BANNER_PITY_LIMIT = 3          # after 3 non-limited Mythics, next one is limited

MAX_MYTHIC_PITY = MYTHIC_GUARANTEE - 1
MAX_LEGENDARY_PITY = LEGENDARY_GUARANTEE - 1
MAX_BANNER_PITY = BANNER_PITY_LIMIT

# Cumulative threshold: of the remaining Mythics (not banner, not boosted),
# half are Legacy Pieces and half are regular champions.
LEGACY_PIECE_THRESHOLD = (
    MYTHIC_BANNER_CHANCE
    + MYTHIC_BOOSTED_CHANCE
    + (1 - MYTHIC_BANNER_CHANCE - MYTHIC_BOOSTED_CHANCE) / 2
)

# Extra banner shards on non-limited Mythics
SHARD_ODDS = ((2, 0.60), (3, 0.35), (5, 0.05))
GUARANTEED_SHARD_PULLS = 6     # first 6 non-limited Mythics always give shards
LATE_SHARD_CHANCE = 1 / 3      # 7th onwards

MYTHIC, LEGENDARY, EPIC = "Mythic", "Legendary", "Epic"
CHAMPION, LEGACY = "champion", "legacy"

DATA_DIR = Path(__file__).with_name("data")
DEFAULT_PROFILE = "old"


# --------------------------------------------------------------------------
# Data files: pools.json + banners.json
# --------------------------------------------------------------------------
class DataFileError(ValueError):
    """A data file is missing, malformed or inconsistent. See .problems."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


def _label(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DataFileError([f"{_label(path)}: file not found (looked in {path.parent})."])
    except json.JSONDecodeError as exc:
        raise DataFileError([f"{_label(path)}: not valid JSON: {exc}"])


@dataclass(frozen=True)
class Pools:
    """Regular (non-banner) items, one tuple per rarity/kind."""
    mythic_champions: tuple[str, ...]
    mythic_legacy: tuple[str, ...]
    legendary_champions: tuple[str, ...]
    legendary_legacy: tuple[str, ...]
    epic_champions: tuple[str, ...]
    epic_legacy: tuple[str, ...]
    item_info: dict[str, tuple[str, str]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        info: dict[str, tuple[str, str]] = {}
        for names, rarity, kind in (
            (self.mythic_champions, MYTHIC, CHAMPION),
            (self.mythic_legacy, MYTHIC, LEGACY),
            (self.legendary_champions, LEGENDARY, CHAMPION),
            (self.legendary_legacy, LEGENDARY, LEGACY),
            (self.epic_champions, EPIC, CHAMPION),
            (self.epic_legacy, EPIC, LEGACY),
        ):
            for name in names:
                info[name] = (rarity, kind)
        object.__setattr__(self, "item_info", info)


_POOL_LAYOUT = (  # (json section, json key, Pools field)
    ("mythic", "champions", "mythic_champions"),
    ("mythic", "legacy", "mythic_legacy"),
    ("legendary", "champions", "legendary_champions"),
    ("legendary", "legacy", "legendary_legacy"),
    ("epic", "champions", "epic_champions"),
    ("epic", "legacy", "epic_legacy"),
)


def load_pools(path: str | Path) -> Pools:
    """
    Read pools.json:
        {"mythic":    {"champions": [...], "legacy": [...]},
         "legendary": {"champions": [...], "legacy": [...]},
         "epic":      {"champions": [...], "legacy": [...]}}
    Every list must be non-empty and a name may appear only once overall
    (case-insensitive, because loaded draw files are matched that way).
    """
    path = Path(path)
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise DataFileError([f"{_label(path)}: must be an object with 'mythic', 'legendary' and 'epic'."])

    problems: list[str] = []
    fields: dict[str, tuple[str, ...]] = {}
    seen: dict[str, str] = {}
    for section, key, attr in _POOL_LAYOUT:
        where = f"{section}.{key}"
        block = raw.get(section)
        names = block.get(key) if isinstance(block, dict) else None
        if not isinstance(names, list) or not names:
            problems.append(f"{_label(path)}: '{where}' must be a non-empty list.")
            continue
        clean: list[str] = []
        for n in names:
            if not isinstance(n, str) or not n.strip():
                problems.append(f"{_label(path)}: '{where}' contains an empty or non-text entry.")
                continue
            n = n.strip()
            if n.casefold() in seen:
                problems.append(f"{_label(path)}: {n!r} in '{where}' already appears in '{seen[n.casefold()]}'.")
            else:
                seen[n.casefold()] = where
            clean.append(n)
        fields[attr] = tuple(clean)

    if problems:
        raise DataFileError(problems)
    return Pools(**fields)


def load_banners(path: str | Path, pools: Pools) -> dict[str, tuple[str, ...]]:
    """
    Read banners.json -> {banner champion: (boosted champions...)}, in file order.
        [ {"name": "Superman", "boosted": ["Cyborg", "The Flash", "Shazam"]}, ... ]
    Boosted champions must be in pools.json under mythic.champions.
    """
    path = Path(path)
    raw = _read_json(path)
    if not isinstance(raw, list) or not raw:
        raise DataFileError([f"{_label(path)}: must be a non-empty list of banners."])

    pool_names = {n.casefold() for n in pools.item_info}
    problems: list[str] = []
    banners: dict[str, tuple[str, ...]] = {}
    for i, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            problems.append(f"{_label(path)}: entry #{i} must be an object with 'name' and 'boosted'.")
            continue
        name, boosted = entry.get("name"), entry.get("boosted")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{_label(path)}: entry #{i} is missing 'name'.")
            continue
        where = f"{_label(path)}: banner {name!r}"
        if name in banners:
            problems.append(f"{where} is listed more than once.")
        if name.casefold() in pool_names:
            problems.append(f"{where} clashes with an item in this profile's pools.json.")
        if not isinstance(boosted, list) or not boosted:
            problems.append(f"{where}: 'boosted' must be a non-empty list.")
            continue
        if len(set(boosted)) != len(boosted):
            problems.append(f"{where}: duplicate names in 'boosted'.")
        for champ in boosted:
            if champ not in pools.mythic_champions:
                hint = difflib.get_close_matches(str(champ), pools.mythic_champions, n=1)
                tip = f" Did you mean {hint[0]!r}?" if hint else ""
                problems.append(f"{where}: {champ!r} is not in this profile's pools.json mythic.champions.{tip}")
        if not [c for c in pools.mythic_champions if c not in boosted]:
            problems.append(f"{where}: needs at least one non-boosted Mythic champion in the pool.")
        banners[name] = tuple(boosted)

    if problems:
        raise DataFileError(problems)
    return banners


@dataclass(frozen=True)
class Milestone:
    """Reward for reaching `pulls` pulls on the current banner."""
    pulls: int
    reward: str
    banner_shards: int = 0   # optional: banner champion shards it gives


@dataclass(frozen=True)
class Rules:
    """Per-server rules."""
    name: str = ""
    extra_shards: bool = True            # bonus banner shards on non-limited Mythics
    milestones: tuple[Milestone, ...] = ()  # sorted by pulls


def load_rules(path: str | Path) -> Rules:
    """
    Read rules.json (optional: a missing file means the default rules):
        {"name": "New servers",
         "extra_shards": false,
         "milestones": [ {"pulls": 50, "reward": "Epic Shards x10", "banner_shards": 0}, ... ]}
    'banner_shards' is optional. Milestones count pulls on the current banner.
    """
    path = Path(path)
    if not path.exists():
        return Rules()
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise DataFileError([f"{_label(path)}: must be an object."])

    problems: list[str] = []
    for key in sorted(set(raw) - {"name", "extra_shards", "milestones"}):
        problems.append(f"{_label(path)}: unknown key {key!r}.")
    name = raw.get("name", "")
    if not isinstance(name, str):
        problems.append(f"{_label(path)}: 'name' must be text.")
        name = ""
    extra = raw.get("extra_shards", True)
    if not isinstance(extra, bool):
        problems.append(f"{_label(path)}: 'extra_shards' must be true or false.")
        extra = True

    milestones: list[Milestone] = []
    raw_ms = raw.get("milestones", [])
    if not isinstance(raw_ms, list):
        problems.append(f"{_label(path)}: 'milestones' must be a list.")
        raw_ms = []
    seen: set[int] = set()
    for i, m in enumerate(raw_ms, 1):
        where = f"{_label(path)}: milestone #{i}"
        if not isinstance(m, dict):
            problems.append(f"{where} must be an object.")
            continue
        for key in sorted(set(m) - {"pulls", "reward", "banner_shards"}):
            problems.append(f"{where}: unknown key {key!r}.")
        pulls, reward, shards = m.get("pulls"), m.get("reward"), m.get("banner_shards", 0)
        is_int = lambda v: isinstance(v, int) and not isinstance(v, bool)
        if not is_int(pulls) or pulls < 1:
            problems.append(f"{where}: 'pulls' must be a whole number >= 1.")
            continue
        if pulls in seen:
            problems.append(f"{where}: another milestone already uses {pulls} pulls.")
        seen.add(pulls)
        if not isinstance(reward, str) or not reward.strip():
            problems.append(f"{where}: 'reward' must be text.")
            continue
        if not is_int(shards) or shards < 0:
            problems.append(f"{where}: 'banner_shards' must be a whole number >= 0.")
            continue
        milestones.append(Milestone(pulls, reward.strip(), shards))

    if problems:
        raise DataFileError(problems)
    return Rules(name.strip(), extra, tuple(sorted(milestones, key=lambda m: m.pulls)))


@dataclass(frozen=True)
class Catalog:
    """Everything a DrawState needs to know about one server profile."""
    pools: Pools
    banners: dict[str, tuple[str, ...]]
    rules: Rules = Rules()

    def item_info(self, name: str) -> tuple[str, str] | None:
        """(rarity, kind) of an item; banner champions are Mythic champions."""
        if name in self.banners:
            return (MYTHIC, CHAMPION)
        return self.pools.item_info.get(name)


def list_profiles(data_dir: str | Path = DATA_DIR) -> list[str]:
    """Server profiles = sub-folders with pools.json and banners.json."""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        return []
    found = sorted(
        d.name for d in data_dir.iterdir()
        if d.is_dir() and (d / "pools.json").exists() and (d / "banners.json").exists()
    )
    if DEFAULT_PROFILE in found:
        found.remove(DEFAULT_PROFILE)
        found.insert(0, DEFAULT_PROFILE)
    return found


def load_catalog(profile: str = DEFAULT_PROFILE, data_dir: str | Path = DATA_DIR) -> Catalog:
    folder = Path(data_dir) / profile
    if not folder.is_dir():
        raise DataFileError([f"Server profile {profile!r} not found in {Path(data_dir)}."])
    pools = load_pools(folder / "pools.json")
    banners = load_banners(folder / "banners.json", pools)
    return Catalog(pools, banners, load_rules(folder / "rules.json"))


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SessionParams:
    """Where the player's pity counters stand before the first pull."""
    mythic_pity: int = 0
    legendary_pity: int = 0
    non_banner_since_last: int = 0   # non-limited Mythics since last limited
    non_limited_pulled: int = 0      # non-limited Mythics pulled on this banner
    pulls_on_banner: int = 0         # pulls already done on this banner (milestones)


@dataclass(frozen=True)
class Pull:
    rarity: str
    item: str
    kind: str                  # CHAMPION | LEGACY
    extra_shards: int = 0      # bonus banner shards
    is_banner: bool = False    # the limited Mythic itself
    forced_mythic: bool = False
    forced_banner: bool = False
    milestones: tuple[Milestone, ...] = ()   # milestones reached by this pull


def roll_extra_shards(non_limited_pulled: int, rng: random.Random) -> int:
    """Bonus banner shards for a non-limited Mythic (count includes this pull)."""
    if non_limited_pulled <= GUARANTEED_SHARD_PULLS or rng.random() < LATE_SHARD_CHANCE:
        roll = rng.random()
        acc = 0.0
        for shards, odds in SHARD_ODDS:
            acc += odds
            if roll < acc:
                return shards
        return SHARD_ODDS[-1][0]
    return 0


# --------------------------------------------------------------------------
# Draw state
# --------------------------------------------------------------------------
class DrawState:
    """One drawing session: pity counters, results and history."""

    def __init__(
        self,
        banner: str | None = None,
        params: SessionParams | None = None,
        rng: random.Random | None = None,
        catalog: Catalog | None = None,
    ):
        self.catalog = catalog or load_catalog()
        self.banner = self._check_banner(banner or next(iter(self.catalog.banners)))
        self.params = params or SessionParams()
        self.rng = rng or random.Random()
        self._reset_counters()

    def _check_banner(self, banner: str) -> str:
        if banner not in self.catalog.banners:
            raise ValueError(f"Unknown banner: {banner!r}")
        return banner

    def _reset_counters(self) -> None:
        p = self.params
        self.mythic_pity = p.mythic_pity
        self.legendary_pity = p.legendary_pity
        self.non_banner_since_last = p.non_banner_since_last
        self.non_limited_pulled = p.non_limited_pulled
        self.pulls_on_banner = p.pulls_on_banner
        self.forced_mythic_count = 0
        self.forced_banner_count = 0
        self.results: Counter[str] = Counter()
        self.history: list[Pull] = []

    # ---- configuration --------------------------------------------------
    @property
    def pools(self) -> Pools:
        return self.catalog.pools

    @property
    def banners(self) -> dict[str, tuple[str, ...]]:
        return self.catalog.banners

    @property
    def rules(self) -> Rules:
        return self.catalog.rules

    @property
    def boosted(self) -> tuple[str, ...]:
        return self.catalog.banners[self.banner]

    def item_info(self, name: str) -> tuple[str, str] | None:
        return self.catalog.item_info(name)

    def reset(
        self,
        banner: str | None = None,
        params: SessionParams | None = None,
        catalog: Catalog | None = None,
    ) -> None:
        """Start over, optionally with a new banner / starting pity / catalog."""
        if catalog is not None:
            self.catalog = catalog
        if banner is not None:
            self.banner = banner
        self._check_banner(self.banner)
        if params is not None:
            self.params = params
        self._reset_counters()

    # ---- drawing --------------------------------------------------------
    def draw(self) -> Pull:
        rng, pools = self.rng, self.pools
        self.mythic_pity += 1
        self.legendary_pity += 1
        roll = rng.random()

        forced_mythic = False
        if self.mythic_pity >= MYTHIC_GUARANTEE:
            rarity = MYTHIC
            forced_mythic = True
            self.forced_mythic_count += 1
        elif self.legendary_pity >= LEGENDARY_GUARANTEE:
            rarity = MYTHIC if roll < MYTHIC_RATE else LEGENDARY
        elif roll < MYTHIC_RATE:
            rarity = MYTHIC
        elif roll < MYTHIC_RATE + LEGENDARY_RATE:
            rarity = LEGENDARY
        else:
            rarity = EPIC

        if rarity == MYTHIC:
            pull = self._draw_mythic(forced_mythic)
        elif rarity == LEGENDARY:
            self.legendary_pity = 0
            item, kind = self._pick_50_50(pools.legendary_champions, pools.legendary_legacy)
            pull = Pull(LEGENDARY, item, kind)
        else:
            item, kind = self._pick_50_50(pools.epic_champions, pools.epic_legacy)
            pull = Pull(EPIC, item, kind)

        self.pulls_on_banner += 1
        reached = tuple(m for m in self.rules.milestones if m.pulls == self.pulls_on_banner)
        if reached:
            pull = replace(pull, milestones=reached)

        self.results[pull.item] += 1
        self.history.append(pull)
        return pull

    def draw_many(self, n: int) -> list[Pull]:
        return [self.draw() for _ in range(n)]

    def _pick_50_50(self, champions, legacy) -> tuple[str, str]:
        if self.rng.random() < 0.5:
            return self.rng.choice(champions), CHAMPION
        return self.rng.choice(legacy), LEGACY

    def _draw_mythic(self, forced_mythic: bool) -> Pull:
        rng, pools = self.rng, self.pools
        self.mythic_pity = 0
        self.legendary_pity = 0

        # Guaranteed limited after 3 non-limited Mythics in a row
        if self.non_banner_since_last >= BANNER_PITY_LIMIT:
            self.non_banner_since_last = 0
            self.forced_banner_count += 1
            return Pull(MYTHIC, self.banner, CHAMPION, 0, True, forced_mythic, True)

        r = rng.random()
        if r < MYTHIC_BANNER_CHANCE:
            self.non_banner_since_last = 0
            return Pull(MYTHIC, self.banner, CHAMPION, 0, True, forced_mythic)

        if r < MYTHIC_BANNER_CHANCE + MYTHIC_BOOSTED_CHANCE:
            item, kind = rng.choice(self.boosted), CHAMPION
        elif r < LEGACY_PIECE_THRESHOLD:
            item, kind = rng.choice(pools.mythic_legacy), LEGACY
        else:
            pool = [c for c in pools.mythic_champions
                    if c not in self.boosted and c != self.banner]
            item, kind = rng.choice(pool), CHAMPION

        self.non_banner_since_last += 1
        self.non_limited_pulled += 1
        extra = roll_extra_shards(self.non_limited_pulled, rng) if self.rules.extra_shards else 0
        return Pull(MYTHIC, item, kind, extra, False, forced_mythic)

    # ---- milestones -----------------------------------------------------
    def reached_milestones(self) -> list[Milestone]:
        return [m for m in self.rules.milestones if m.pulls <= self.pulls_on_banner]

    def next_milestone(self) -> Milestone | None:
        return next((m for m in self.rules.milestones if m.pulls > self.pulls_on_banner), None)

    # ---- derived stats --------------------------------------------------
    @property
    def total_pulls(self) -> int:
        return len(self.history)

    @property
    def rarity_totals(self) -> Counter[str]:
        totals = Counter({MYTHIC: 0, LEGENDARY: 0, EPIC: 0})
        for pull in self.history:
            totals[pull.rarity] += 1
        return totals

    @property
    def extra_shards_total(self) -> int:
        return sum(p.extra_shards for p in self.history)

    @property
    def banner_pulls(self) -> int:
        return self.results.get(self.banner, 0)

    def grouped_results(self) -> dict[str, list[tuple[str, int]]]:
        """Items pulled, grouped by rarity+kind, each sorted by count (desc)."""
        order = [
            (MYTHIC, CHAMPION), (MYTHIC, LEGACY),
            (LEGENDARY, CHAMPION), (LEGENDARY, LEGACY),
            (EPIC, CHAMPION), (EPIC, LEGACY),
        ]
        groups: dict[str, list[tuple[str, int]]] = {}
        for rarity, kind in order:
            label = f"{rarity} {'Champions' if kind == CHAMPION else 'Legacy Pieces'}"
            items = [(n, c) for n, c in self.results.items() if self.item_info(n) == (rarity, kind)]
            items.sort(key=lambda x: (-x[1], x[0]))
            groups[label] = items
        return groups
