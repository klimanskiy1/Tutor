"""SQLite: карточки (синхронизированы из JSON), прогресс FSRS, лог ответов."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "tutor.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    deck_id  TEXT NOT NULL,
    card_id  TEXT NOT NULL,
    content  TEXT NOT NULL,      -- JSON карточки как в файле колоды
    position INTEGER NOT NULL,   -- порядок в файле (порядок показа новых)
    active   INTEGER NOT NULL DEFAULT 1,  -- 0 если карточка пропала из файла
    PRIMARY KEY (deck_id, card_id)
);

CREATE TABLE IF NOT EXISTS progress (
    deck_id  TEXT NOT NULL,
    card_id  TEXT NOT NULL,
    fsrs     TEXT NOT NULL,      -- fsrs.Card.to_dict()
    due      TEXT NOT NULL,      -- ISO UTC, дублируется для быстрых запросов
    state    INTEGER NOT NULL,   -- fsrs.State: 1 learning, 2 review, 3 relearning
    reps     INTEGER NOT NULL DEFAULT 0,
    lapses   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (deck_id, card_id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id     TEXT NOT NULL,
    card_id     TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,   -- ISO UTC
    day         TEXT NOT NULL,   -- локальная дата YYYY-MM-DD (для дневных лимитов)
    rating      INTEGER NOT NULL,
    correct     INTEGER,         -- 1/0/NULL (NULL для самооценки)
    was_new     INTEGER NOT NULL DEFAULT 0,
    answer      TEXT,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS reviews_day ON reviews(deck_id, day);
CREATE INDEX IF NOT EXISTS progress_due ON progress(deck_id, due);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
