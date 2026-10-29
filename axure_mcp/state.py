"""Tiny state file so a relaunched Axure can come back to the document that was open."""
from __future__ import annotations

import json
import os

from .paths import data_dir

STATE_DIR = data_dir()
STATE_PATH = os.path.join(STATE_DIR, "axure-mcp-state.json")


def load() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(**fields) -> None:
    data = load()
    data.update({k: v for k, v in fields.items() if v})
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass


def last_file() -> str | None:
    path = load().get("file")
    return path if path and os.path.exists(path) else None
