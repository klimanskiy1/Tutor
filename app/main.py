"""HTTP API + раздача статики. Запуск: python run.py"""
import json
import random
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, decks, grading, scheduler

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Tutor")
conn = db.connect()
db.init(conn)
lock = threading.Lock()
sync_report = decks.sync_all(conn)


@app.exception_handler(decks.DeckError)
async def deck_error(_, exc: decks.DeckError):
    return JSONResponse({"error": str(exc)}, status_code=400)


def deck_meta(deck_id: str) -> dict:
    d = decks.load_deck(deck_id)
    d["new_per_day"] = int(d.get("new_per_day") or decks.DEFAULT_NEW_PER_DAY)
    return d


def public_card(content: dict, card_id: str) -> dict:
    """Карточка без правильного ответа — для показа вопроса."""
    hidden = {"answer", "accept", "explanation"}
    out = {k: v for k, v in content.items() if k not in hidden}
    out["id"] = card_id
    return out


# ---------- колоды ----------

@app.get("/api/decks")
def api_decks():
    out = []
    for deck_id in decks.list_deck_ids():
        if deck_id in sync_report["errors"]:
            out.append({"id": deck_id, "name": deck_id, "error": sync_report["errors"][deck_id]})
            continue
        d = deck_meta(deck_id)
        with lock:
            counts = scheduler.deck_counts(conn, deck_id, d["new_per_day"])
        out.append({"id": deck_id, "name": d["name"], "description": d.get("description", ""),
                    "new_per_day": d["new_per_day"], **counts})
    return out


@app.post("/api/reload")
def api_reload():
    global sync_report
    with lock:
        sync_report = decks.sync_all(conn)
    return sync_report


class DeckIn(BaseModel):
    id: str | None = None
    name: str
    description: str = ""
    new_per_day: int = decks.DEFAULT_NEW_PER_DAY
    cards: list[dict[str, Any]] = []


@app.post("/api/decks")
def api_create_deck(body: DeckIn):
    deck_id = body.id or decks.slugify(body.name)
    if decks.deck_path(deck_id).exists():
        raise decks.DeckError(f"колода {deck_id} уже есть")
    decks.save_deck(deck_id, body.model_dump(exclude={"id"}))
    api_reload()
    return {"id": deck_id}


@app.get("/api/decks/{deck_id}")
def api_get_deck(deck_id: str):
    d = deck_meta(deck_id)
    for c in d["cards"]:
        c["id"] = decks.card_key(c)
    return d


@app.put("/api/decks/{deck_id}")
def api_save_deck(deck_id: str, body: DeckIn):
    decks.deck_path(deck_id)  # валидация id
    decks.save_deck(deck_id, body.model_dump(exclude={"id"}))
    api_reload()
    return {"ok": True}


@app.delete("/api/decks/{deck_id}")
def api_delete_deck(deck_id: str):
    p = decks.deck_path(deck_id)
    if p.exists():
        p.unlink()
    with lock:
        conn.execute("DELETE FROM cards WHERE deck_id=?", (deck_id,))
        conn.execute("DELETE FROM progress WHERE deck_id=?", (deck_id,))
        conn.execute("DELETE FROM reviews WHERE deck_id=?", (deck_id,))
        conn.commit()
    api_reload()
    return {"ok": True}


@app.post("/api/decks/{deck_id}/reset")
def api_reset_deck(deck_id: str):
    with lock:
        conn.execute("DELETE FROM progress WHERE deck_id=?", (deck_id,))
        conn.execute("DELETE FROM reviews WHERE deck_id=?", (deck_id,))
        conn.commit()
    return {"ok": True}


# ---------- учёба ----------

def parse_deck_ids(s: str) -> list[str]:
    ids = [x for x in s.split(",") if x]
    if not ids:
        raise decks.DeckError("не указаны колоды")
    return ids


@app.get("/api/study/next")
def api_study_next(decks_: str = Query(alias="decks"), cram: bool = False, exclude: str | None = None):
    """Следующая карточка по одной или нескольким колодам (decks=a,b,c).

    Приоритет: просроченные повторы (самые старые), потом новые — случайная колода из тех,
    где ещё есть новые (разделы перемешиваются, как на экзамене), потом в режиме cram — ближайшие по due.
    exclude = "deck/card" — не показывать эту карточку два раза подряд в cram.
    """
    ids = parse_deck_ids(decks_)
    metas = {i: deck_meta(i) for i in ids}
    ex_deck, ex_card = (exclude.split("/", 1) if exclude and "/" in exclude else (None, exclude))
    now = scheduler.now_utc().isoformat()
    with lock:
        counts = {"total": 0, "new": 0, "new_total": 0, "due": 0, "learning": 0, "next_due": None}
        candidates = []
        for i, m in metas.items():
            c = scheduler.deck_counts(conn, i, m["new_per_day"])
            for k in ("total", "new", "new_total", "due", "learning"):
                counts[k] += c[k]
            if c["next_due"] and (counts["next_due"] is None or c["next_due"] < counts["next_due"]):
                counts["next_due"] = c["next_due"]
            row = scheduler.next_card(conn, i, m["new_per_day"], ignore_limits=cram,
                                      exclude=ex_card if i == ex_deck else None)
            if row is not None:
                kind = 1 if row["fsrs"] is None else (0 if row["due"] <= now else 2)
                candidates.append((kind, row["due"] or "", random.random(), i, row))
        if not candidates:
            return {"card": None, "counts": counts}
        candidates.sort(key=lambda t: t[:3])
        _, _, _, deck_id, row = candidates[0]
        intervals = scheduler.preview_intervals(row)
    content = json.loads(row["content"])
    return {
        "card": public_card(content, row["card_id"]),
        "deck_id": deck_id,
        "deck_name": metas[deck_id]["name"],
        "is_new": row["fsrs"] is None,
        "state": row["state"],
        "reps": row["reps"],
        "intervals": intervals,
        "counts": counts,
    }


@app.get("/api/decks/{deck_id}/next")
def api_next(deck_id: str, cram: bool = False, exclude: str | None = None):
    return api_study_next(deck_id, cram, exclude)


@app.get("/api/study/cards")
def api_study_cards(decks_: str = Query(alias="decks")):
    """Все карточки колод с ответами, в порядке файлов — для режима просмотра."""
    out = []
    for deck_id in parse_deck_ids(decks_):
        d = deck_meta(deck_id)
        for n, c in enumerate(d["cards"], 1):
            out.append({"deck_id": deck_id, "deck_name": d["name"], "n": n, "total": len(d["cards"]),
                        "card": {**c, "id": decks.card_key(c)}})
    return out


class CheckIn(BaseModel):
    card_id: str
    answer: Any = None


@app.post("/api/decks/{deck_id}/check")
def api_check(deck_id: str, body: CheckIn):
    with lock:
        row = conn.execute(
            "SELECT content FROM cards WHERE deck_id=? AND card_id=?", (deck_id, body.card_id)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "нет карточки")
    card = json.loads(row["content"])
    res = grading.check(card, body.answer)
    res["answer"] = card["answer"]
    res["accept"] = card.get("accept") or []
    res["explanation"] = card.get("explanation", "")
    return res


class AnswerIn(BaseModel):
    card_id: str
    rating: int  # 1 Again, 2 Hard, 3 Good, 4 Easy
    correct: bool | None = None
    answer: str | None = None
    duration_ms: int | None = None


@app.post("/api/decks/{deck_id}/answer")
def api_answer(deck_id: str, body: AnswerIn):
    if body.rating not in (1, 2, 3, 4):
        raise HTTPException(400, "rating 1..4")
    with lock:
        exists = conn.execute(
            "SELECT 1 FROM cards WHERE deck_id=? AND card_id=? AND active=1", (deck_id, body.card_id)
        ).fetchone()
        if not exists:
            raise HTTPException(404, "нет карточки")
        return scheduler.apply_review(
            conn, deck_id, body.card_id, body.rating, body.correct, body.answer, body.duration_ms
        )


@app.get("/api/stats")
def api_stats():
    with lock:
        days = conn.execute(
            """SELECT day, COUNT(*) AS n, SUM(COALESCE(correct, rating>1)) AS ok
               FROM reviews GROUP BY day ORDER BY day DESC LIMIT 30"""
        ).fetchall()
    return [dict(r) for r in days]


# ---------- статика ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


decks.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=decks.MEDIA_DIR), name="media")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
