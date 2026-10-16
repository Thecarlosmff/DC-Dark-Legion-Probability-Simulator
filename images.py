"""
Local image store and downloader (no Streamlit).

Images live in static/images/ (so Streamlit can also serve them to the browser, see
.streamlit/config.toml) with the same layout and file names as the desktop Teambuilder,
so its data/images folder can simply be copied in:

    champions/cards/<Name>_card.<ext>    legacy/<Name>.<ext>    faction/<Name>.<ext>
    class/<Name>.<ext>                   backgrounds/<any>.png|jpg
"""
from __future__ import annotations

import io
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from urllib.parse import quote, urlsplit, urlunsplit

from PIL import Image

if TYPE_CHECKING:
    from teambuilder import TeamData

IMAGES_DIR = Path(__file__).with_name("static") / "images"
STATIC_URL_PREFIX = "app/static/images"        # where Streamlit serves IMAGES_DIR
FOLDERS = {"card": "champions/cards", "legacy": "legacy", "faction": "faction",
           "class": "class", "background": "backgrounds"}
KIND_LABELS = {"card": "Champion cards", "legacy": "Legacy pieces", "faction": "Faction icons",
               "class": "Class icons", "background": "Backgrounds"}
IMAGE_EXTENSIONS = (".png", ".webp", ".jpg", ".jpeg")
CLASS_ICON_URL = "https://dcdl-companion.com/img/classes/{}.png"
# Same Google Drive backgrounds as the desktop Teambuilder
BACKGROUND_URLS = tuple(f"https://drive.google.com/uc?export=download&id={i}" for i in (
    "1IeOi9WGQnQMi1Z0QCkP9MpB6cs2vrK0i", "1InqaIu0zOwjO7RYHwPwFE3MYlpiDEYsl",
    "1e0qIiQOufU2e3uYKp0b8IoareO9Zlwwg"))


def safe_name(name: str) -> str:
    """Champion / piece name -> file-system safe name (same rule as the desktop app)."""
    name = re.sub(r"[^\w\s-]", "", name.strip())
    return re.sub(r"\s+", "_", name)


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------
class ImageStore:
    """Finds stored images (any extension, case-insensitive)."""

    def __init__(self, root: str | Path = IMAGES_DIR):
        self.root = Path(root)
        self._index: dict[str, dict[str, Path]] = {}

    def folder(self, kind: str) -> Path:
        return self.root / FOLDERS[kind]

    def refresh(self) -> None:
        self._index.clear()

    def _files(self, kind: str) -> dict[str, Path]:
        if kind not in self._index:
            files: dict[str, Path] = {}
            folder = self.folder(kind)
            if folder.is_dir():
                for p in sorted(folder.iterdir()):
                    if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file() and p.stat().st_size > 0:
                        files.setdefault(p.stem.casefold(), p)
            self._index[kind] = files
        return self._index[kind]

    @staticmethod
    def stem(kind: str, name: str) -> str:
        return f"{safe_name(name)}_card" if kind == "card" else safe_name(name)

    def path(self, kind: str, name: str) -> Path | None:
        return self._files(kind).get(self.stem(kind, name).casefold())

    def url(self, kind: str, name: str) -> str | None:
        """URL Streamlit serves the stored image at (needs enableStaticServing)."""
        p = self.path(kind, name)
        if p is None:
            return None
        return f"{STATIC_URL_PREFIX}/{FOLDERS[kind]}/{quote(p.name)}"

    def backgrounds(self) -> list[Path]:
        return sorted(self._files("background").values())


# --------------------------------------------------------------------------
# What is needed / what is missing
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ImageJob:
    kind: str
    name: str
    url: str

    @property
    def filename(self) -> str:
        ext = Path(urlsplit(self.url).path).suffix.lower()
        ext = ext if ext in IMAGE_EXTENSIONS else ".png"
        return f"{ImageStore.stem(self.kind, self.name)}{ext}"


def wanted(data: "TeamData") -> list[ImageJob]:
    """Every image the app can use, with its source URL."""
    jobs = [ImageJob("card", c.name, c.card_url) for c in data.champions.values() if c.card_url]
    jobs += [ImageJob("legacy", p.name, p.image_url) for p in data.legacy.values() if p.image_url]
    jobs += [ImageJob("faction", s.name, s.image_url) for s in data.synergies.values() if s.image_url]
    jobs += [ImageJob("class", c, CLASS_ICON_URL.format(quote(c))) for c in data.classes]
    jobs += [ImageJob("background", f"bg_{i:02d}", url) for i, url in enumerate(BACKGROUND_URLS, 1)]
    return jobs


def missing(data: "TeamData", store: ImageStore) -> list[ImageJob]:
    return [j for j in wanted(data) if store.path(j.kind, j.name) is None]


def status(data: "TeamData", store: ImageStore) -> dict[str, tuple[int, int]]:
    """kind -> (stored, wanted)."""
    out: dict[str, list[int]] = {k: [0, 0] for k in FOLDERS}
    for j in wanted(data):
        out[j.kind][1] += 1
        out[j.kind][0] += store.path(j.kind, j.name) is not None
    return {k: (v[0], v[1]) for k, v in out.items()}


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------
def fetch_bytes(url: str, timeout: float = 20.0) -> bytes | None:
    if not url.startswith(("http://", "https://")):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 teambuilder"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(25_000_000)
    except Exception:
        return None


def _is_image(raw: bytes) -> bool:
    try:
        Image.open(io.BytesIO(raw)).verify()
        return True
    except Exception:
        return False


def _clean(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/%"),
                       parts.query, parts.fragment))


def _variants(url: str) -> list[str]:
    cleaned = _clean(url)
    seen, out = set(), []
    for u in (cleaned, cleaned.replace("%27", ""), url.replace("'", "")):   # apostrophe quirks
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


@dataclass
class DownloadReport:
    downloaded: int = 0
    failed: list[tuple[ImageJob, str]] = field(default_factory=list)


def download_images(
    jobs: list[ImageJob],
    store: ImageStore,
    *,
    fetch: Callable[[str], bytes | None] = fetch_bytes,
    workers: int = 8,
    progress: Callable[[float], None] | None = None,
) -> DownloadReport:
    """Download the jobs into the store (skips files already stored)."""
    report = DownloadReport()

    def run(job: ImageJob) -> str | None:
        if store.path(job.kind, job.name) is not None:
            return None
        for url in _variants(job.url):
            raw = fetch(url)
            if raw and _is_image(raw):
                dest = store.folder(job.kind) / job.filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".part")
                tmp.write_bytes(raw)
                os.replace(tmp, dest)
                return None
        return "could not download a valid image"

    if jobs:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(run, j): j for j in jobs}
            for i, fut in enumerate(as_completed(futures), 1):
                error = fut.result()
                if error:
                    report.failed.append((futures[fut], error))
                else:
                    report.downloaded += 1
                if progress:
                    progress(i / len(jobs))
    store.refresh()
    return report
