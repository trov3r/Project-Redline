from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
DB_FILE = BASE_DIR / "database.json"
AUC_DB_FILE = BASE_DIR / "auction.json"
FONT_PATH = ASSETS_DIR / "fonts" / "PenitentiaryGothicFill.ttf"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GAME_TIMEZONE = os.getenv("GAME_TIMEZONE", "Europe/Moscow").strip()

BOT_USERNAME = os.getenv("BOT_USERNAME", "ProjectRedlineBot").strip().lstrip("@")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/ProjectRedline").strip()
DOCS_URL = os.getenv(
    "DOCS_URL",
    "https://telegra.ph/Dokumentaciya-po-komandam-igrovogo-telegram-bota-Project-Redline-04-30",
).strip()


def _parse_int_set(value: str, fallback: str) -> set[int]:
    raw = value.strip() or fallback
    result: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            result.add(int(chunk))
        except ValueError:
            continue
    return result


def _parse_upper_set(value: str, fallback: str) -> set[str]:
    raw = value.strip() or fallback
    return {chunk.strip().upper() for chunk in raw.split(",") if chunk.strip()}


EXCLUSIVE_IDS = _parse_int_set(
    os.getenv("EXCLUSIVE_IDS", ""),
    "",
)

RESERVED_PLATES = _parse_upper_set(
    os.getenv("RESERVED_PLATES", ""),
    "REDL1NE,1ABC123",
)

STARTGROUP_URL = f"https://t.me/{BOT_USERNAME}?startgroup=true"
