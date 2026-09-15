"""Колоды — JSON-файлы в decks/. Файл = источник истины, БД хранит только прогресс.

Формат файла decks/<id>.json:
{
  "name": "Физика, тема 3",
  "description": "необязательно",
  "new_per_day": 20,              # необязательно, по умолчанию 20
  "cards": [
    {"id": "q1", "type": "choice", "question": "...", "options": ["a","b"], "answer": 0},
    {"id": "q2", "type": "multi", "question": "...", "options": ["a","b","c"], "answer": [0, 2]},
    {"id": "q3", "type": "truefalse", "question": "...", "answer": true},
    {"id": "q4", "type": "text", "question": "...", "answer": "Париж", "accept": ["paris"]},
    {"id": "q5", "type": "flash", "question": "лицевая", "answer": "оборотная"}
  ]
}
Необязательные поля карточки: "explanation", "image" (путь относительно decks/media/), "tags".
"id" можно не указывать — тогда он считается из текста вопроса (при правке вопроса прогресс сбросится).
"""
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .db import ROOT

DECKS_DIR = ROOT / "decks"
MEDIA_DIR = DECKS_DIR / "media"

CARD_TYPES = {"choice", "multi", "truefalse", "text", "flash"}
DEFAULT_NEW_PER_DAY = 20


class DeckError(ValueError):
    pass


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "-", name).strip("-").lower()
    return s or "deck"


def card_key(card: dict) -> str:
    if card.get("id"):
        return str(card["id"])
    h = hashlib.sha1(f"{card.get('type')}|{card.get('question', '')}".encode()).hexdigest()
    return h[:12]


def validate_card(card: dict, idx: int) -> None:
    t = card.get("type")
    if t not in CARD_TYPES:
        raise DeckError(f"карточка #{idx}: неизвестный type={t!r}")
    if not str(card.get("question", "")).strip() and not card.get("image"):
        raise DeckError(f"карточка #{idx}: пустой question")
    ans = card.get("answer")
    if t in ("choice", "multi"):
        opts = card.get("options")
        if not isinstance(opts, list) or len(opts) < 2:
            raise DeckError(f"карточка #{idx}: нужно минимум 2 options")
        if t == "choice":
            if not isinstance(ans, int) or not 0 <= ans < len(opts):
                raise DeckError(f"карточка #{idx}: answer должен быть индексом опции")
        else:
            if not isinstance(ans, list) or not ans or any(
                not isinstance(a, int) or not 0 <= a < len(opts) for a in ans
            ):
                raise DeckError(f"карточка #{idx}: answer должен быть списком индексов")
    elif t == "truefalse":
        if not isinstance(ans, bool):
            raise DeckError(f"карточка #{idx}: answer должен быть true/false")
    else:  # text, flash
        if not str(ans if ans is not None else "").strip():
            raise DeckError(f"карточка #{idx}: пустой answer")


def validate_deck(deck: dict) -> None:
    if not str(deck.get("name", "")).strip():
        raise DeckError("у колоды нет name")
    cards = deck.get("cards")
    if not isinstance(cards, list):
        raise DeckError("cards должен быть списком")
    seen: set[str] = set()
    for i, c in enumerate(cards, 1):
        if not isinstance(c, dict):
            raise DeckError(f"карточка #{i}: не объект")
        validate_card(c, i)
        k = card_key(c)
        if k in seen:
            raise DeckError(f"карточка #{i}: дублируется id {k!r}")
        seen.add(k)


def deck_path(deck_id: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9а-яА-ЯёЁ_.-]+", deck_id):
        raise DeckError("плохой id колоды")
    return DECKS_DIR / f"{deck_id}.json"


def load_deck(deck_id: str) -> dict:
    p = deck_path(deck_id)
    if not p.exists():
        raise DeckError(f"колоды {deck_id} нет")
    with p.open(encoding="utf-8") as f:
        deck = json.load(f)
    deck["id"] = deck_id
    return deck


def save_deck(deck_id: str, deck: dict) -> None:
    validate_deck(deck)
    out = {k: v for k, v in deck.items() if k != "id"}
    DECKS_DIR.mkdir(exist_ok=True)
    with deck_path(deck_id).open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")


def list_deck_ids() -> list[str]:
    DECKS_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in DECKS_DIR.glob("*.json"))


def sync_all(conn: sqlite3.Connection) -> dict[str, Any]:
    """Читает все decks/*.json и обновляет таблицу cards. Прогресс не трогает."""
    result: dict[str, Any] = {"decks": [], "errors": {}}
    present: set[str] = set()
    for deck_id in list_deck_ids():
        try:
            deck = load_deck(deck_id)
            validate_deck(deck)
        except (DeckError, json.JSONDecodeError) as e:
            result["errors"][deck_id] = str(e)
            continue
        present.add(deck_id)
        keys = []
        for pos, card in enumerate(deck["cards"]):
            key = card_key(card)
            keys.append(key)
            conn.execute(
                """INSERT INTO cards(deck_id, card_id, content, position, active)
                   VALUES (?,?,?,?,1)
                   ON CONFLICT(deck_id, card_id) DO UPDATE SET
                     content=excluded.content, position=excluded.position, active=1""",
                (deck_id, key, json.dumps(card, ensure_ascii=False), pos),
            )
        if keys:
            q = ",".join("?" * len(keys))
            conn.execute(
                f"UPDATE cards SET active=0 WHERE deck_id=? AND card_id NOT IN ({q})",
                (deck_id, *keys),
            )
        else:
            conn.execute("UPDATE cards SET active=0 WHERE deck_id=?", (deck_id,))
        result["decks"].append(deck_id)
    # колоды, чьи файлы удалили
    rows = conn.execute("SELECT DISTINCT deck_id FROM cards WHERE active=1").fetchall()
    for r in rows:
        if r["deck_id"] not in present:
            conn.execute("UPDATE cards SET active=0 WHERE deck_id=?", (r["deck_id"],))
    conn.commit()
    return result
