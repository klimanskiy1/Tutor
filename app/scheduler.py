"""Интервальное повторение на FSRS (тот же алгоритм, что в современном Anki) + очередь."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from fsrs import Card, Rating, Scheduler, State

from .decks import DEFAULT_NEW_PER_DAY

# desired_retention 0.9 — стандарт Anki. Шаги обучения: 1 мин, 10 мин; переучивания: 10 мин.
_scheduler = Scheduler(desired_retention=0.9)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def local_day(dt: datetime | None = None) -> str:
    return (dt or now_utc()).astimezone().strftime("%Y-%m-%d")


def _load_card(row: sqlite3.Row | None) -> Card:
    if row is None or row["fsrs"] is None:
        return Card()
    return Card.from_dict(json.loads(row["fsrs"]))


def preview_intervals(row: sqlite3.Row | None) -> dict[int, str]:
    """Что будет с карточкой при каждой оценке — для подписей на кнопках."""
    base = _load_card(row)
    now = now_utc()
    out = {}
    for r in (Rating.Again, Rating.Hard, Rating.Good, Rating.Easy):
        c, _ = _scheduler.review_card(Card.from_dict(base.to_dict()), r, now)
        out[int(r)] = human_delta(c.due - now)
    return out


def human_delta(td: timedelta) -> str:
    s = td.total_seconds()
    if s < 60:
        return "<1м"
    if s < 3600:
        return f"{round(s / 60)}м"
    if s < 86400:
        return f"{round(s / 3600)}ч"
    d = s / 86400
    if d < 30:
        return f"{round(d)}д"
    if d < 365:
        return f"{d / 30:.1f}мес"
    return f"{d / 365:.1f}г"


def apply_review(
    conn: sqlite3.Connection,
    deck_id: str,
    card_id: str,
    rating: int,
    correct: bool | None,
    answer: str | None,
    duration_ms: int | None,
) -> dict:
    row = conn.execute(
        "SELECT * FROM progress WHERE deck_id=? AND card_id=?", (deck_id, card_id)
    ).fetchone()
    was_new = row is None
    card = _load_card(row)
    now = now_utc()
    card, _log = _scheduler.review_card(card, Rating(rating), now, duration_ms)
    reps = (row["reps"] if row else 0) + 1
    lapses = (row["lapses"] if row else 0) + (1 if rating == 1 and not was_new else 0)
    conn.execute(
        """INSERT INTO progress(deck_id, card_id, fsrs, due, state, reps, lapses)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(deck_id, card_id) DO UPDATE SET
             fsrs=excluded.fsrs, due=excluded.due, state=excluded.state,
             reps=excluded.reps, lapses=excluded.lapses""",
        (deck_id, card_id, json.dumps(card.to_dict()), card.due.isoformat(), int(card.state), reps, lapses),
    )
    conn.execute(
        """INSERT INTO reviews(deck_id, card_id, reviewed_at, day, rating, correct, was_new, answer, duration_ms)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (deck_id, card_id, now.isoformat(), local_day(now), rating,
         None if correct is None else int(correct), int(was_new), answer, duration_ms),
    )
    conn.commit()
    return {"due": card.due.isoformat(), "in": human_delta(card.due - now), "state": int(card.state)}


def new_done_today(conn: sqlite3.Connection, deck_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM reviews WHERE deck_id=? AND day=? AND was_new=1",
        (deck_id, local_day()),
    ).fetchone()[0]


def deck_counts(conn: sqlite3.Connection, deck_id: str, new_per_day: int = DEFAULT_NEW_PER_DAY) -> dict:
    now = now_utc().isoformat()
    total = conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id=? AND active=1", (deck_id,)).fetchone()[0]
    new_total = conn.execute(
        """SELECT COUNT(*) FROM cards c LEFT JOIN progress p USING(deck_id, card_id)
           WHERE c.deck_id=? AND c.active=1 AND p.card_id IS NULL""",
        (deck_id,),
    ).fetchone()[0]
    due = conn.execute(
        """SELECT COUNT(*) FROM progress p JOIN cards c USING(deck_id, card_id)
           WHERE p.deck_id=? AND c.active=1 AND p.due<=? AND p.state=?""",
        (deck_id, now, int(State.Review)),
    ).fetchone()[0]
    learning = conn.execute(
        """SELECT COUNT(*) FROM progress p JOIN cards c USING(deck_id, card_id)
           WHERE p.deck_id=? AND c.active=1 AND p.state IN (?,?)""",
        (deck_id, int(State.Learning), int(State.Relearning)),
    ).fetchone()[0]
    next_due = conn.execute(
        """SELECT MIN(p.due) FROM progress p JOIN cards c USING(deck_id, card_id)
           WHERE p.deck_id=? AND c.active=1 AND p.due>?""",
        (deck_id, now),
    ).fetchone()[0]
    new_left = max(0, min(new_total, new_per_day - new_done_today(conn, deck_id)))
    return {
        "total": total,
        "new": new_left,
        "new_total": new_total,
        "due": due,
        "learning": learning,
        "next_due": next_due,
    }


def next_card(conn: sqlite3.Connection, deck_id: str, new_per_day: int,
              ignore_limits: bool = False, exclude: str | None = None):
    """Возвращает (row cards, row progress|None) или None если учить нечего.

    Порядок: просроченные повторы/обучение (самые старые сначала), потом новые по порядку в файле.
    """
    now = now_utc().isoformat()
    row = conn.execute(
        """SELECT c.*, p.fsrs, p.due, p.state, p.reps, p.lapses
           FROM progress p JOIN cards c USING(deck_id, card_id)
           WHERE p.deck_id=? AND c.active=1 AND p.due<=?
           ORDER BY p.due ASC LIMIT 1""",
        (deck_id, now),
    ).fetchone()
    if row:
        return row
    if ignore_limits or new_done_today(conn, deck_id) < new_per_day:
        row = conn.execute(
            """SELECT c.*, NULL AS fsrs, NULL AS due, NULL AS state, 0 AS reps, 0 AS lapses
               FROM cards c LEFT JOIN progress p USING(deck_id, card_id)
               WHERE c.deck_id=? AND c.active=1 AND p.card_id IS NULL
               ORDER BY c.position ASC LIMIT 1""",
            (deck_id,),
        ).fetchone()
        if row:
            return row
    if ignore_limits:
        # режим «учить всё»: берём ближайшую по due, даже если ещё рано
        row = conn.execute(
            """SELECT c.*, p.fsrs, p.due, p.state, p.reps, p.lapses
               FROM progress p JOIN cards c USING(deck_id, card_id)
               WHERE p.deck_id=? AND c.active=1 AND c.card_id<>?
               ORDER BY p.due ASC LIMIT 1""",
            (deck_id, exclude or ""),
        ).fetchone()
        if row:
            return row
    return None
