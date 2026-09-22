"""Where nanoSay keeps what you gave it to read, and what it made of it.

One folder in your home directory, plain files, no database. Delete the folder
and nanoSay forgets everything.

The reader never says your text out loud to anybody but you, and never sends it
anywhere. The only thing that ever leaves this folder is a file you download,
or a request to nanoLaama if — and only if — you press the button that asks it
to shorten something.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

APP_DIR_NAME = ".nanosay"
_lock = threading.Lock()

KEEP_OUTPUTS_DAYS = 7

DEFAULT_SETTINGS = {
    "voice": "",              # the voice id chosen last time
    "rate": None,             # None → whatever this engine considers normal
    "volume": 100,
    "shorten_first": False,   # ask nanoLaama for a short version before reading
    "address": "http://127.0.0.1:8760",   # where nanoLaama lives, if it is running
    "keep_days": KEEP_OUTPUTS_DAYS,
}


# --------------------------------------------------------------------- folders
def data_dir() -> Path:
    """The one folder nanoSay owns. Point it elsewhere with NANOSAY_HOME."""
    override = os.environ.get("NANOSAY_HOME")
    path = Path(override) if override else Path.home() / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_dir() -> Path:
    path = data_dir() / "given"
    path.mkdir(parents=True, exist_ok=True)
    return path


def recordings_dir() -> Path:
    """Saved readings, ready to play or send to somebody."""
    path = data_dir() / "recordings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def scratch_dir() -> Path:
    """Half-finished pieces, kept out of the way of finished ones."""
    path = data_dir() / "working"
    path.mkdir(parents=True, exist_ok=True)
    return path


# -------------------------------------------------------------------- settings
def _settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    with _lock:
        settings = dict(DEFAULT_SETTINGS)
        try:
            with open(_settings_path(), "r", encoding="utf-8") as handle:
                stored = json.load(handle)
            if isinstance(stored, dict):
                settings.update({key: stored[key] for key in DEFAULT_SETTINGS if key in stored})
        except (OSError, json.JSONDecodeError):
            pass
        return settings


def save_settings(patch: dict) -> dict:
    settings = load_settings()
    for key, value in (patch or {}).items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key == "rate" and value is not None:
            try:
                settings["rate"] = int(value)
            except (TypeError, ValueError):
                continue
        elif isinstance(DEFAULT_SETTINGS[key], bool):
            settings[key] = bool(value)
        elif isinstance(DEFAULT_SETTINGS[key], int) and not isinstance(DEFAULT_SETTINGS[key], bool):
            try:
                settings[key] = int(value)
            except (TypeError, ValueError):
                continue
        elif isinstance(value, str):
            settings[key] = value.strip()[:200]
    with _lock:
        tmp = _settings_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        os.replace(tmp, _settings_path())
    return settings


# --------------------------------------------------------------------- library
def _library_path() -> Path:
    return data_dir() / "library.json"


def load_library(limit: int = 60) -> list:
    """The things you have asked it to read, newest first."""
    with _lock:
        try:
            with open(_library_path(), "r", encoding="utf-8") as handle:
                items = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return []
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        audio = Path(str(item.get("audio", "")))
        out.append({**item, "here": bool(item.get("audio")) and audio.is_file()})
    return out[:limit]


def remember(entry: dict) -> None:
    items = load_library(limit=200)
    items.insert(0, {**entry, "when": time.time()})
    with _lock:
        try:
            tmp = _library_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(items[:200], indent=2), encoding="utf-8")
            os.replace(tmp, _library_path())
        except OSError:
            pass
    tidy()


def forget(entry_name: str = "") -> list:
    items = load_library(limit=200)
    if entry_name:
        items = [item for item in items if item.get("audio_name") != entry_name]
    else:
        items = []
    with _lock:
        try:
            _library_path().write_text(json.dumps(items, indent=2), encoding="utf-8")
        except OSError:
            pass
    return items


# ---------------------------------------------------------------------- tidying
def tidy(keep_days: int | None = None) -> dict:
    """Delete recordings older than `keep_days`.

    A recording of a long document is a large file, and nobody wants the disk to
    fill up quietly. Anything still recent is left alone.
    """
    days = KEEP_OUTPUTS_DAYS if keep_days is None else int(keep_days)
    if days <= 0:
        return {"removed": 0, "freed_mb": 0.0}
    cutoff = time.time() - days * 86400
    removed, freed = 0, 0
    for folder in (recordings_dir(), scratch_dir()):
        for path in folder.iterdir():
            try:
                stat = path.stat()
                if stat.st_mtime >= cutoff or not path.is_file():
                    continue
                freed += stat.st_size
                path.unlink()
                removed += 1
            except OSError:
                continue
    return {"removed": removed, "freed_mb": round(freed / (1024 * 1024), 1)}


def clear_recordings() -> dict:
    removed, freed = 0, 0
    for folder in (recordings_dir(), scratch_dir()):
        for path in folder.iterdir():
            try:
                if path.is_file():
                    freed += path.stat().st_size
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    forget()
    return {"removed": removed, "freed_mb": round(freed / (1024 * 1024), 1)}


def free_name(name: str, folder: Path | None = None) -> Path:
    """A path in `folder` that is not taken yet."""
    folder = folder or recordings_dir()
    stem = Path(name).stem or "reading"
    suffix = Path(name).suffix
    candidate = folder / (stem + suffix)
    counter = 2
    while candidate.exists():
        candidate = folder / ("%s-%d%s" % (stem, counter, suffix))
        counter += 1
    return candidate


def clear_given_files() -> None:
    """Dropped documents are copies — the originals are still where you kept them."""
    for path in uploads_dir().iterdir():
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            continue


def free_space_mb() -> int:
    try:
        import shutil
        return shutil.disk_usage(str(data_dir())).free // (1024 * 1024)
    except OSError:
        return -1
