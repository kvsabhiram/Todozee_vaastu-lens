"""
Vastu rules database layer.

The folder `vastu_database 1/` holds one JSON per language. Every file shares
the same structure (metadata / rooms / unknown) and — crucially — the same room
ORDER, even though non-English files use translated room keys. So we map the
classifier's English slug (e.g. "master_bedroom") to a translated room entry by
position, using the English file as the canonical index.

The English file is also the single source of truth for the YOLO detection
weights (`detection_rules`), so the classifier and the rules can never drift.

Public API:
    available_languages()          -> {code: DisplayName}
    detection_rules()              -> {slug: {class: weight}}   (for the classifier)
    room_order()                   -> [slug, ...]               (canonical order)
    get_rules(slug, language)      -> localized room entry (or the "unknown" entry)
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).parent.resolve()
DB_DIR = HERE.parent / "vastu_database_1"  # data folder lives at the project root
CANONICAL_LANG = "English"  # English keys define the slugs + detection weights

# ISO-639 code -> language name as it appears in the filename.
LANG_CODES = {
    "as":  "Assamese",
    "bn":  "Bengali",
    "brx": "Bodo",
    "en":  "English",
    "gu":  "Gujarati",
    "hi":  "Hindi",
    "kn":  "Kannada",
    "ks":  "Kashmiri",
    "kok": "Konkani",
    "mai": "Maithili",
    "ml":  "Malayalam",
    "mni": "Manipuri",
    "mr":  "Marathi",
    "ne":  "Nepali",
    "or":  "Odia",
    "pa":  "Punjabi",
    "sd":  "Sindhi",
    "ta":  "Tamil",
    "te":  "Telugu",
    "ur":  "Urdu",
}
_NAME_TO_CODE = {name.lower(): code for code, name in LANG_CODES.items()}


def _resolve_language(language: str) -> str:
    """Accept an ISO code ('hi'), a language name ('Hindi'), or 'en'/'english'.

    Returns the canonical language NAME used in the filename. Falls back to
    English for anything unrecognized.
    """
    if not language:
        return CANONICAL_LANG
    key = language.strip().lower()
    if key in LANG_CODES:                 # an ISO code
        return LANG_CODES[key]
    if key in _NAME_TO_CODE:              # a language name
        return LANG_CODES[_NAME_TO_CODE[key]]
    return CANONICAL_LANG


@lru_cache(maxsize=None)
def _load(language_name: str) -> dict:
    path = DB_DIR / f"vastu_translation_{language_name}.json"
    if not path.exists():
        raise FileNotFoundError(f"No rules file for language: {language_name} ({path})")
    return json.loads(path.read_text(encoding="utf-8"))


def available_languages() -> list[str]:
    """List of language names (e.g. "Telugu", "Hindi") present on disk.

    The name itself is the language code callers pass in.
    """
    return [name for name in LANG_CODES.values()
            if (DB_DIR / f"vastu_translation_{name}.json").exists()]


@lru_cache(maxsize=1)
def room_order() -> tuple[str, ...]:
    """Canonical English room slugs, in the order shared by every language file."""
    return tuple(_load(CANONICAL_LANG)["rooms"].keys())


@lru_cache(maxsize=1)
def detection_rules() -> dict[str, dict[str, float]]:
    """{slug: {yolo_class: weight}} pulled straight from the English DB.

    This is what the classifier scores against, so detection and rules stay in sync.
    """
    rooms = _load(CANONICAL_LANG)["rooms"]
    return {slug: dict(entry["detection_rules"]) for slug, entry in rooms.items()}


def badge_colors() -> dict[str, tuple[int, int, int]]:
    """{slug: (B,G,R)} badge colors from the English DB, plus an 'unknown' fallback."""
    db = _load(CANONICAL_LANG)
    colors = {slug: tuple(entry["badge_color_bgr"]) for slug, entry in db["rooms"].items()}
    colors["unknown"] = tuple(db["unknown"]["badge_color_bgr"])
    return colors


def get_rules(slug: str, language: str = "en") -> dict:
    """Return the localized room entry for an English `slug` in `language`.

    For `slug == "unknown"` (or anything not in the room list) returns the file's
    own `unknown` block, so callers always get a usable, translated payload.
    """
    lang_name = _resolve_language(language)
    db = _load(lang_name)

    order = room_order()
    if slug not in order:
        return {"slug": "unknown", "language": lang_name, **db["unknown"]}

    idx = order.index(slug)
    lang_room_keys = list(db["rooms"].keys())
    entry = db["rooms"][lang_room_keys[idx]]

    # Drop internal-only fields the UI doesn't need.
    public = {k: v for k, v in entry.items()
              if k not in ("detection_rules", "badge_color_bgr")}
    return {"slug": slug, "language": lang_name, **public}
