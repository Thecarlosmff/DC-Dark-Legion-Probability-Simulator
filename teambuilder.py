"""
Team builder logic (no Streamlit): champion data, roster (what you own),
teams, synergies and team images.

Data lives in data/teambuilder/:
    champions.json, synergies.json, legacy_pieces.json   game data (from dcdl-companion)
    owned.json                                           YOUR roster: [{"name", "stars", "color"}]
Both the plain list format and {"updated_at": ..., "<key>": [...]} are accepted.
"""
from __future__ import annotations

import io
import json
import math
import random
import re
import textwrap
import urllib.request
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image, ImageColor, ImageDraw, ImageFont, PngImagePlugin

from engine import DataFileError
from images import ImageStore

DEFAULT_TEAMS = 5      # teams shown at the start ("Add team" creates more)
MAX_TEAMS = 30
MAX_PER_TEAM = 5
TEAM_DATA_DIR = Path(__file__).with_name("data") / "teambuilder"
TEAMS_DIR = Path(__file__).with_name("teams")      # team images are saved here
RARITIES = ("Epic", "Legendary", "Mythic", "Limited Mythic", "Iconic")

TIERS = ("locked", "unlocked", "white", "blue", "purple", "gold", "red")
TIER_COLORS = {
    "locked": "#888888", "unlocked": "#a0a0a0", "white": "#ffffff",
    "blue": "#4da6ff", "purple": "#a855f7", "gold": "#facc15", "red": "#ff5555",
}
PNG_KEY = "dc_team"   # same metadata key as the desktop Teambuilder


# --------------------------------------------------------------------------
# Game data
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Champion:
    name: str
    champ_class: str
    rarity: str
    synergies: tuple[str, ...] = ()
    is_healer: bool = False
    card_url: str = ""
    image_url: str = ""
    codex_url: str = ""
    avg_level: str = ""
    related: tuple[str, ...] = ()


@dataclass(frozen=True)
class LegacyPiece:
    name: str
    rarity: str
    classes: tuple[str, ...]
    image_url: str = ""


@dataclass(frozen=True)
class SynergyTier:
    count: int
    description: str
    applies_to: str = ""


@dataclass(frozen=True)
class Synergy:
    name: str
    description: str
    tiers: tuple[SynergyTier, ...]   # sorted by count
    image_url: str = ""


@dataclass(frozen=True)
class TeamData:
    champions: dict[str, Champion]
    legacy: dict[str, LegacyPiece]
    synergies: dict[str, Synergy]

    @property
    def factions(self) -> list[str]:
        return sorted({s for c in self.champions.values() for s in c.synergies})

    @property
    def classes(self) -> list[str]:
        return sorted({c.champ_class for c in self.champions.values() if c.champ_class})

    def legacy_for(self, champion: Champion) -> list[LegacyPiece]:
        """Legacy pieces the champion's class can equip."""
        cls = champion.champ_class.casefold()
        return sorted((p for p in self.legacy.values()
                       if cls in (c.casefold() for c in p.classes)), key=lambda p: p.name)


def _label(path: Path) -> str:
    return f"teambuilder/{path.name}"


def _read_items(path: Path, key: str, problems: list[str]) -> list[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append(f"{_label(path)}: file not found (looked in {path.parent}).")
        return []
    except json.JSONDecodeError as exc:
        problems.append(f"{_label(path)}: not valid JSON: {exc}")
        return []
    if isinstance(raw, dict):
        raw = raw.get(key)
    if not isinstance(raw, list):
        problems.append(f"{_label(path)}: expected a list (or an object with '{key}').")
        return []
    return [x for x in raw if isinstance(x, dict)]


def _strs(value) -> tuple[str, ...]:
    return tuple(str(v) for v in value) if isinstance(value, list) else ()


def load_team_data(folder: str | Path = TEAM_DATA_DIR) -> TeamData:
    folder = Path(folder)
    problems: list[str] = []

    champions: dict[str, Champion] = {}
    seen: set[str] = set()
    for item in _read_items(folder / "champions.json", "champions", problems):
        name = str(item.get("name", "")).strip()
        if not name:
            problems.append("teambuilder/champions.json: a champion has no name.")
            continue
        if name.casefold() in seen:
            problems.append(f"teambuilder/champions.json: {name!r} appears more than once.")
            continue
        seen.add(name.casefold())
        champions[name] = Champion(
            name=name, champ_class=str(item.get("class", "")),
            rarity=str(item.get("baseRarity", "")),
            synergies=_strs(item.get("inherentSynergies")),
            is_healer=bool(item.get("isHealer", False)),
            card_url=str(item.get("cardImageUrl") or ""),
            image_url=str(item.get("defaultImageUrl") or ""),
            codex_url=str(item.get("codexUrl") or ""),
            avg_level=str(item.get("communityAverageLevel") or ""),
            related=_strs(item.get("relatedChampions")),
        )

    legacy: dict[str, LegacyPiece] = {}
    for item in _read_items(folder / "legacy_pieces.json", "legacy_pieces", problems):
        name = str(item.get("name", "")).strip()
        if name:
            legacy[name] = LegacyPiece(name, str(item.get("baseRarity", "")),
                                       _strs(item.get("classes")),
                                       str(item.get("defaultImageUrl") or ""))

    synergies: dict[str, Synergy] = {}
    for item in _read_items(folder / "synergies.json", "synergies", problems):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        tiers = []
        for t in item.get("tiers", []):
            if not isinstance(t, dict) or not isinstance(t.get("countRequired"), int):
                continue
            # a few tiers use "description" instead of "tierDescription"
            text = t.get("tierDescription") or t.get("description") or ""
            tiers.append(SynergyTier(t["countRequired"], str(text), str(t.get("appliesTo", ""))))
        synergies[name] = Synergy(name, str(item.get("description", "")),
                                  tuple(sorted(tiers, key=lambda t: t.count)),
                                  str(item.get("factionImageUrl") or ""))

    if not champions and not problems:
        problems.append("teambuilder/champions.json has no champions.")
    if problems:
        raise DataFileError(problems)
    return TeamData(champions, legacy, synergies)


# --------------------------------------------------------------------------
# Roster (what you own): {champion name: {"color": tier, "stars": 0-5}}
# --------------------------------------------------------------------------
Roster = dict[str, dict]


def normalize_entry(color: str, stars: int) -> dict:
    color = str(color).strip().lower()
    if color not in TIERS:
        color = next((t for t in TIERS if t in color), "locked")
    if color in ("locked", "unlocked"):
        return {"color": color, "stars": 0}
    try:
        stars = int(stars)
    except (TypeError, ValueError):
        stars = 0
    return {"color": color, "stars": max(0, min(5, stars))}


def default_roster(data: TeamData) -> Roster:
    return {name: normalize_entry("locked", 0) for name in data.champions}


def parse_roster(raw, data: TeamData) -> tuple[Roster, list[str]]:
    """Roster from owned.json content. Returns (roster, names not in the game data)."""
    roster = default_roster(data)
    unknown: list[str] = []
    items = raw.get("champions", []) if isinstance(raw, dict) else raw
    for entry in items if isinstance(items, list) else []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        name = str(entry["name"])
        if name in roster:
            roster[name] = normalize_entry(entry.get("color", "locked"), entry.get("stars", 0))
        else:
            unknown.append(name)
    return roster, unknown


def load_roster(path: str | Path, data: TeamData) -> Roster:
    """Roster from a file; everything locked if it is missing or broken."""
    try:
        return parse_roster(json.loads(Path(path).read_text(encoding="utf-8")), data)[0]
    except (OSError, json.JSONDecodeError):
        return default_roster(data)


def roster_json(roster: Roster, data: TeamData) -> str:
    """owned.json text (same format the desktop Teambuilder uses)."""
    rows = [{"name": n, "stars": roster.get(n, {}).get("stars", 0),
             "color": roster.get(n, {}).get("color", "locked")} for n in sorted(data.champions)]
    return json.dumps(rows, indent=4, ensure_ascii=False)


def save_roster(path: str | Path, roster: Roster, data: TeamData) -> None:
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(roster_json(roster, data), encoding="utf-8")
    tmp.replace(path)


def rank_label(entry: dict) -> str:
    color, stars = entry.get("color", "locked"), entry.get("stars", 0)
    if color in ("locked", "unlocked"):
        return color.capitalize()
    return f"{color.capitalize()} {'★' * stars}" if stars else color.capitalize()


def rank_color(entry: dict) -> str:
    return TIER_COLORS.get(entry.get("color", "locked"), TIER_COLORS["locked"])


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------
def filter_champions(
    data: TeamData,
    roster: Roster,
    *,
    query: str = "",
    rarities: Iterable[str] | None = None,
    factions: Iterable[str] = (),
    faction_mode: str = "AND",
    classes: Iterable[str] = (),
    hide_locked: bool = False,
    exclude: Iterable[str] = (),
) -> list[Champion]:
    q = query.strip().casefold()
    rarities = set(rarities) if rarities is not None else None
    factions, classes, exclude = set(factions), set(classes), set(exclude)
    out = []
    for c in data.champions.values():
        if q and q not in c.name.casefold() and q not in c.champ_class.casefold():
            continue
        if c.name in exclude:
            continue
        if hide_locked and roster.get(c.name, {}).get("color", "locked") == "locked":
            continue
        if rarities is not None and c.rarity not in rarities:
            continue
        if factions:
            own = set(c.synergies)
            if (not factions <= own) if faction_mode == "AND" else (not own & factions):
                continue
        if classes and c.champ_class not in classes:
            continue
        out.append(c)
    return out


# --------------------------------------------------------------------------
# Teams
# --------------------------------------------------------------------------
@dataclass
class Team:
    uid: int                               # stable id (widget keys), unlike the position
    title: str = ""
    subtitle: str = ""
    show_rank: bool = False
    members: list[str] = field(default_factory=list)
    legacy: dict[str, str] = field(default_factory=dict)

    def display_title(self, index: int) -> str:
        return self.title.strip() or f"Team {index + 1}"


class TeamsState:
    """Teams of up to MAX_PER_TEAM champions, each with a name and legacy pieces."""

    def __init__(self, n_teams: int = DEFAULT_TEAMS):
        self.teams: list[Team] = []
        self._next_uid = 0
        for _ in range(n_teams):
            self.add_team()

    def add_team(self) -> int | None:
        """New empty team. Returns its index (None at the limit)."""
        if len(self.teams) >= MAX_TEAMS:
            return None
        self.teams.append(Team(self._next_uid, f"Team {len(self.teams) + 1}"))
        self._next_uid += 1
        return len(self.teams) - 1

    def remove_team(self, index: int) -> None:
        """Delete a team (the last remaining one is only emptied)."""
        if len(self.teams) > 1:
            del self.teams[index]
        else:
            self.clear(index)

    def add(self, index: int, name: str) -> bool:
        members = self.teams[index].members
        if name in members or len(members) >= MAX_PER_TEAM:
            return False
        members.append(name)
        return True

    def remove(self, index: int, name: str) -> None:
        team = self.teams[index]
        if name in team.members:
            team.members.remove(name)
        team.legacy.pop(name, None)

    def clear(self, index: int) -> None:
        self.teams[index].members.clear()
        self.teams[index].legacy.clear()

    def set_legacy(self, index: int, name: str, piece: str | None) -> None:
        team = self.teams[index]
        if name not in team.members:
            return
        if piece:
            team.legacy[name] = piece
        else:
            team.legacy.pop(name, None)

    def set_team(self, index: int, names: Iterable[str], legacy: dict[str, str],
                 data: TeamData, *, title: str | None = None, subtitle: str | None = None,
                 show_rank: bool | None = None) -> list[str]:
        """Replace a team's champions (e.g. loaded from an image). Returns what was skipped."""
        team = self.teams[index]
        skipped: list[str] = []
        members: list[str] = []
        for n in names:
            if n in data.champions and n not in members and len(members) < MAX_PER_TEAM:
                members.append(n)
            else:
                skipped.append(n)
        team.members = members
        team.legacy = {}
        for champ, piece in (legacy or {}).items():
            if champ in members and piece in data.legacy and \
                    champ_can_equip(data, data.champions[champ], piece):
                team.legacy[champ] = piece
        if title is not None:
            team.title = title
        if subtitle is not None:
            team.subtitle = subtitle
        if show_rank is not None:
            team.show_rank = show_rank
        return skipped

    def used_names(self) -> set[str]:
        return {n for t in self.teams for n in t.members}


def champ_can_equip(data: TeamData, champion: Champion, piece: str) -> bool:
    return any(p.name == piece for p in data.legacy_for(champion))


@dataclass(frozen=True)
class SynergyStatus:
    name: str
    count: int
    tier: SynergyTier | None        # highest tier reached
    next_tier: SynergyTier | None   # next tier to reach


def team_synergies(names: Iterable[str], data: TeamData) -> list[SynergyStatus]:
    """Faction counts of a team with the active / next bonus, biggest first."""
    counts: Counter[str] = Counter()
    for n in names:
        if n in data.champions:
            counts.update(data.champions[n].synergies)
    out = []
    for name, count in counts.items():
        tiers = data.synergies[name].tiers if name in data.synergies else ()
        reached = [t for t in tiers if t.count <= count]
        upcoming = [t for t in tiers if t.count > count]
        out.append(SynergyStatus(name, count, reached[-1] if reached else None,
                                 upcoming[0] if upcoming else None))
    return sorted(out, key=lambda s: (-s.count, s.name))


# --------------------------------------------------------------------------
# Team images (PNG with the team embedded, compatible with the desktop app)
# --------------------------------------------------------------------------
_IMAGE_CACHE: dict[str, bytes] = {}


def fetch_url(url: str, timeout: float = 8.0) -> bytes | None:
    """Download an image (http/https only). Successful downloads are cached."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    if url in _IMAGE_CACHE:
        return _IMAGE_CACHE[url]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 teambuilder"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(8_000_000)
    except Exception:
        return None
    _IMAGE_CACHE[url] = body
    return body


def _open(raw: bytes | None) -> Image.Image | None:
    if not raw:
        return None
    try:
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _star(radius: int, fill, outline, supersample: int = 4) -> Image.Image:
    size = int(radius * 2.6)
    big = size * supersample
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    cx = cy = big / 2
    outer, inner = radius * supersample, radius * supersample * 0.5
    pts = [(cx + (outer if i % 2 == 0 else inner) * math.cos(math.radians(-90 + i * 36)),
            cy + (outer if i % 2 == 0 else inner) * math.sin(math.radians(-90 + i * 36)))
           for i in range(10)]
    ImageDraw.Draw(img).polygon(pts, fill=fill, outline=outline, width=supersample * 2)
    return img.resize((size, size), Image.LANCZOS)


def _background(size: tuple[int, int], background_dir: Path | None, rng: random.Random) -> Image.Image:
    w, h = size
    files = []
    if background_dir and Path(background_dir).is_dir():
        files = [p for p in Path(background_dir).iterdir()
                 if p.suffix.lower() in (".png", ".jpg", ".jpeg")]
    if files:
        try:
            return Image.open(rng.choice(files)).convert("RGBA").resize(size)
        except Exception:
            pass
    img = Image.new("RGBA", size)   # no background images: dark vertical gradient
    px = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        px.line([(0, y), (w, y)], fill=(int(20 + 40 * t), int(24 + 6 * t), int(44 + 12 * t), 255))
    return img


def render_team_png(
    names: list[str],
    legacy_map: dict[str, str],
    data: TeamData,
    roster: Roster,
    *,
    title: str = "Team",
    subtitle: str = "",
    show_rank: bool = False,
    fetch: Callable[[str], bytes | None] = fetch_url,
    store: ImageStore | None = None,
    background_dir: str | Path | None = None,
    seed: int | None = None,
) -> bytes:
    """
    Team picture: first 2 champions in the front row, the other 3 behind, faction
    counts below. Images come from the local `store` first, then from the internet;
    anything missing is replaced by a name card, so it always works. The team is
    embedded in the PNG ('dc_team') so it can be loaded back.
    """
    def blob(kind: str, name: str, url: str) -> bytes | None:
        local = store.path(kind, name) if store else None
        if local:
            try:
                return local.read_bytes()
            except OSError:
                pass
        return fetch(url)

    if background_dir is None and store is not None:
        background_dir = store.folder("background")
    members = [data.champions[n] for n in names if n in data.champions][:MAX_PER_TEAM]
    card_w, card_h, gap = 180, 255, 30
    canvas_w = 1280
    y1 = 140
    y2 = y1 + card_h + 40
    title_font, sub_font, small_font, name_font = _font(60), _font(32), _font(24), _font(22)

    # faction counts: icon + "xN" (or "Name xN" without icon), wrapped into rows
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    items = []   # (icon or None, label, width)
    for status in team_synergies(names, data):
        icon = _open(blob("faction", status.name, data.synergies[status.name].image_url)) \
            if status.name in data.synergies else None
        label = f"x{status.count}" if icon else f"{status.name} x{status.count}"
        width = (48 if icon else 0) + probe.textbbox((0, 0), label, font=small_font)[2]
        items.append((icon.resize((40, 40)) if icon else None, label, width))
    rows: list[list[tuple]] = [[]]
    for item in items:
        used = sum(i[2] for i in rows[-1]) + 30 * len(rows[-1])
        if rows[-1] and used + item[2] > canvas_w - 100:
            rows.append([])
        rows[-1].append(item)
    canvas_h = y2 + card_h + 20 + max(len(rows), 1) * 52 + 30
    canvas = _background((canvas_w, canvas_h), Path(background_dir) if background_dir else None,
                         random.Random(seed))
    draw = ImageDraw.Draw(canvas)

    def centered(text: str, font, y: int, fill) -> int:
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((canvas_w - (box[2] - box[0])) // 2, y), text, font=font, fill=fill)
        return box[3] - box[1]

    title_h = centered(title or "Team", title_font, 30, (255, 255, 255))
    if subtitle:
        centered(subtitle, sub_font, 30 + title_h + 10, (200, 200, 200))

    def card(champ: Champion) -> Image.Image:
        img = _open(blob("card", champ.name, champ.card_url))
        if img:
            return img.resize((card_w, card_h))
        ph = Image.new("RGBA", (card_w, card_h), (38, 42, 66, 255))
        d = ImageDraw.Draw(ph)
        d.rectangle([0, 0, card_w - 1, card_h - 1], outline=(120, 124, 160, 255), width=3)
        lines = textwrap.wrap(champ.name, 10) or [champ.name]
        y = card_h // 2 - 16 * len(lines)
        for line in lines:   # centered in the area right of the star column
            w = d.textbbox((0, 0), line, font=name_font)[2]
            d.text((34 + (card_w - 34 - w) // 2, y), line, font=name_font, fill=(255, 255, 255))
            y += 32
        w = d.textbbox((0, 0), champ.champ_class, font=small_font)[2]
        d.text((34 + (card_w - 34 - w) // 2, card_h - 78), champ.champ_class, font=small_font,
               fill=(170, 170, 190))
        return ph

    def place_row(row: list[Champion], y: int) -> None:
        total = len(row) * card_w + (len(row) - 1) * gap
        x0 = (canvas_w - total) // 2
        for i, champ in enumerate(row):
            x = x0 + i * (card_w + gap)
            img = card(champ)
            canvas.paste(img, (x, y), img)
            piece = legacy_map.get(champ.name)
            icon = _open(blob("legacy", piece, data.legacy[piece].image_url)) \
                if piece in data.legacy else None
            if icon:
                icon = icon.resize((56, 56))
                canvas.paste(icon, (x + card_w - 62, y + card_h - 62), icon)
            elif piece:   # no picture: small label so the piece is still visible
                draw.rectangle([x, y + card_h - 26, x + card_w, y + card_h], fill=(0, 0, 0, 190))
                draw.text((x + 6, y + card_h - 24), piece[:20], font=_font(16), fill=(255, 255, 255))
            entry = roster.get(champ.name, {})
            if show_rank and entry.get("color", "locked") != "locked":
                filled = ImageColor.getrgb(rank_color(entry)) + (255,)
                for s in range(5):
                    star = _star(11, filled if s < entry.get("stars", 0) else (60, 60, 60, 210),
                                 (25, 25, 25, 230))
                    cy = y + (card_h - (26 * 4 + 22)) // 2 + 11 + s * 26
                    canvas.paste(star, (int(x + 16 - star.size[0] / 2), int(cy - star.size[1] / 2)), star)

    place_row(members[:2], y1)
    place_row(members[2:5], y2)

    # faction rows (centered)
    for r, row in enumerate(rows):
        total = sum(i[2] for i in row) + 30 * (len(row) - 1)
        x = (canvas_w - total) // 2
        y = y2 + card_h + 20 + r * 52
        for icon, label, width in row:
            tx = x
            if icon:
                canvas.paste(icon, (x, y), icon)
                tx = x + 48
            draw.text((tx, y + 6), label, font=small_font, fill=(255, 255, 255))
            x += width + 30

    meta = PngImagePlugin.PngInfo()
    meta.add_text(PNG_KEY, json.dumps({
        "team": [m.name for m in members],
        "legacy": {k: v for k, v in legacy_map.items() if k in {m.name for m in members}},
        "title": title or "", "subtitle": subtitle or ""}))   # extra keys: old app ignores them
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG", pnginfo=meta)
    return out.getvalue()


@dataclass(frozen=True)
class SavedTeam:
    names: list[str]
    legacy: dict[str, str]
    title: str = ""
    subtitle: str = ""


def read_team_png(png: bytes) -> SavedTeam | None:
    """Team embedded in a PNG made by this app or by the desktop Teambuilder."""
    try:
        info = Image.open(io.BytesIO(png)).info
        payload = json.loads(info[PNG_KEY])
        return SavedTeam(
            names=[str(n) for n in payload.get("team", [])],
            legacy={str(k): str(v) for k, v in (payload.get("legacy") or {}).items()},
            title=str(payload.get("title") or ""), subtitle=str(payload.get("subtitle") or ""))
    except Exception:
        return None


def safe_filename(title: str) -> str:
    """'My team: #1' -> 'My_team_1' (empty -> 'Team')."""
    name = re.sub(r"[^\w\s-]", "", title.strip())
    return re.sub(r"\s+", "_", name).strip("_") or "Team"


def save_team_image(png: bytes, title: str, folder: str | Path = TEAMS_DIR) -> Path:
    """Save to <folder>/<title>.png (the team name is the file name; same name replaces)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe_filename(title)}.png"
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(png)
    tmp.replace(path)
    return path


def list_team_images(folder: str | Path = TEAMS_DIR) -> list[Path]:
    folder = Path(folder)
    return sorted((p for p in folder.glob("*.png") if p.is_file()),
                  key=lambda p: p.name.casefold()) if folder.is_dir() else []
