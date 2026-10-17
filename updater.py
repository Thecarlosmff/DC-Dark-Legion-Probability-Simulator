"""
Download / update the game data JSON files from the DCDL Companion API (no Streamlit).

    champions.json, synergies.json, legacy_pieces.json, effects.json  ->  data/teambuilder/

How an update works
  * each endpoint (https://dcdl-companion.com/api/<file>) is fetched and read;
  * the new items are MERGED into the ones you already have, matched by name: the API's
    values win, but fields the API does not send (for example the legacy pieces'
    rarity and image URL, which the API documents only as name + classes) are kept;
  * the whole result is loaded as a test before anything is written, so a bad response
    can never break the app; the previous file is kept as <file>.bak;
  * nothing is written when nothing changed.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import teambuilder as tb
from engine import DataFileError

API_BASE = "https://dcdl-companion.com/api"
# file -> (key of the list when the file is wrapped as {"updated_at": ..., key: [...]})
DATASETS: dict[str, str] = {
    "champions.json": "champions",
    "synergies.json": "synergies",
    "legacy_pieces.json": "legacy_pieces",
    "effects.json": "effects",
}
MIN_KEEP_RATIO = 0.5    # refuse a response with less than half of the items we already have


def fetch_json(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 teambuilder",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(30_000_000).decode("utf-8-sig"))


# --------------------------------------------------------------------------
# Reading / merging
# --------------------------------------------------------------------------
def unwrap(raw: Any, key: str) -> list[dict]:
    """The list of items from a plain list or a {"<key>": [...]} object."""
    if isinstance(raw, dict):
        raw = raw.get(key)
        if raw is None:
            raise ValueError(f"no '{key}' list in the response")
    if not isinstance(raw, list):
        raise ValueError("the response is not a list")
    items = [x for x in raw if isinstance(x, dict)]
    if len(items) != len(raw):
        raise ValueError("the list contains entries that are not objects")
    if any(not str(x.get("name", "")).strip() for x in items):
        raise ValueError("an item has no name")
    return items


def read_existing(path: Path, key: str) -> list[dict]:
    try:
        return unwrap(json.loads(path.read_text(encoding="utf-8")), key)
    except (OSError, ValueError):
        return []


def merge_items(old: list[dict], new: list[dict]) -> list[dict]:
    """New items (API order); fields the API did not send are taken from the old item
    with the same name (duplicates are matched in order)."""
    pool: dict[str, list[dict]] = {}
    for item in old:
        pool.setdefault(str(item["name"]).strip().casefold(), []).append(item)
    merged = []
    for item in new:
        matches = pool.get(str(item["name"]).strip().casefold())
        base = matches.pop(0) if matches else {}
        merged.append({**base, **item})
    return merged


def _names(items: list[dict]) -> list[str]:
    return [str(i["name"]).strip() for i in items]


# --------------------------------------------------------------------------
# Update
# --------------------------------------------------------------------------
@dataclass
class FileResult:
    filename: str
    status: str                      # "updated" | "unchanged" | "failed"
    detail: str = ""
    total: int = 0
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: int = 0                 # items present before and now, with different content


@dataclass
class UpdateReport:
    files: list[FileResult] = field(default_factory=list)
    aborted: str = ""                # why nothing was written (validation failed)

    @property
    def updated(self) -> bool:
        return any(f.status == "updated" for f in self.files)

    @property
    def failed(self) -> list[FileResult]:
        return [f for f in self.files if f.status == "failed"]


def data_updated_at(folder: str | Path = tb.TEAM_DATA_DIR) -> str:
    """Timestamp stored in champions.json by the last update (or by the site)."""
    try:
        raw = json.loads((Path(folder) / "champions.json").read_text(encoding="utf-8"))
        return str(raw.get("updated_at", "")) if isinstance(raw, dict) else ""
    except (OSError, ValueError):
        return ""


def update_data(
    folder: str | Path = tb.TEAM_DATA_DIR,
    *,
    base_url: str = API_BASE,
    fetch: Callable[[str], Any] = fetch_json,
) -> UpdateReport:
    """Fetch every dataset, merge, validate everything, then write what changed."""
    folder = Path(folder)
    report = UpdateReport()
    merged_by_file: dict[str, list[dict]] = {}
    pending: dict[str, FileResult] = {}

    for filename, key in DATASETS.items():
        result = FileResult(filename, "unchanged")
        report.files.append(result)
        path = folder / filename
        old = read_existing(path, key)
        try:
            new = unwrap(fetch(f"{base_url}/{filename}"), key)
        except Exception as exc:                       # network, JSON or format problem
            result.status, result.detail = "failed", f"{type(exc).__name__}: {exc}"
            merged_by_file[filename] = old
            continue
        if not new:
            result.status, result.detail = "failed", "the response has no items"
            merged_by_file[filename] = old
            continue
        if old and len(new) < MIN_KEEP_RATIO * len(old):
            result.status = "failed"
            result.detail = (f"the response has {len(new)} items but you have {len(old)}: "
                             "looks incomplete, kept your file")
            merged_by_file[filename] = old
            continue

        merged = merge_items(old, new)
        merged_by_file[filename] = merged
        result.total = len(merged)
        old_names, new_names = _names(old), _names(merged)
        result.added = [n for n in new_names if n.casefold() not in {o.casefold() for o in old_names}]
        result.removed = [n for n in old_names if n.casefold() not in {x.casefold() for x in new_names}]
        by_old = {}
        for item in old:
            by_old.setdefault(str(item["name"]).strip().casefold(), []).append(item)
        for item in merged:
            same = by_old.get(str(item["name"]).strip().casefold())
            if same and same.pop(0) != item:
                result.changed += 1
        if merged != old:
            result.status = "updated"
            pending[filename] = result

    # Validate the complete result as a test before touching any real file.
    with tempfile.TemporaryDirectory() as tmp:
        for filename, key in DATASETS.items():
            items = merged_by_file.get(filename) or []
            if items:
                (Path(tmp) / filename).write_text(json.dumps({key: items}), encoding="utf-8")
        try:
            tb.load_team_data(tmp)
        except DataFileError as exc:
            report.aborted = "; ".join(exc.problems[:3])
            for result in pending.values():
                result.status, result.detail = "failed", "not written (validation failed)"
            return report

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    folder.mkdir(parents=True, exist_ok=True)
    for filename in pending:
        path = folder / filename
        if path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        payload = {"updated_at": stamp, DATASETS[filename]: merged_by_file[filename]}
        tmp_path = path.with_name(path.name + ".part")
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, path)
    return report
