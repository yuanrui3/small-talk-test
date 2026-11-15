"""Local SQLite storage for the pilot's participants and sessions."""

from contextlib import contextmanager
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import secrets
import sqlite3


SCHEMA = """
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pilot_slot INTEGER NOT NULL DEFAULT 1 UNIQUE CHECK (pilot_slot = 1),
    code_hash TEXT NOT NULL UNIQUE,
    registered_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    local_day TEXT NOT NULL,
    day_number INTEGER NOT NULL CHECK (typeof(day_number) = 'integer' AND day_number BETWEEN 1 AND 14),
    started_at REAL NOT NULL,
    topics TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'chat' CHECK (phase IN ('chat', 'rating', 'readers', 'done', 'crisis')),
    rating INTEGER CHECK (rating IS NULL OR (typeof(rating) = 'integer' AND rating BETWEEN 0 AND 10)),
    satisfaction INTEGER CHECK (satisfaction IS NULL OR (
        typeof(satisfaction) = 'integer' AND satisfaction BETWEEN 0 AND 10 AND day_number IN (1, 14)
    )),
    rated_at REAL,
    CHECK ((phase IN ('readers', 'done')) = (rating IS NOT NULL)),
    CHECK ((rating IS NULL) = (rated_at IS NULL)),
    CHECK (satisfaction IS NULL OR rating IS NOT NULL),
    UNIQUE (participant_id, local_day),
    UNIQUE (participant_id, day_number)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS messages_session_id ON messages (session_id, id);

CREATE TABLE IF NOT EXISTS guesses (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode IN ('cold', 'history')),
    score REAL CHECK (score BETWEEN 0 AND 10),
    confidence REAL CHECK (confidence BETWEEN 0 AND 1),
    cues TEXT NOT NULL,
    error TEXT,
    CHECK (
        (error IS NULL AND score IS NOT NULL AND confidence IS NOT NULL)
        OR (error IS NOT NULL AND score IS NULL AND confidence IS NULL)
    ),
    PRIMARY KEY (session_id, mode)
);

COMMIT;
"""


class PilotAlreadyRegistered(Exception):
    """The single pilot slot is occupied."""


class Storage:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self, *, write: bool = False):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA secure_delete = ON")
            with connection:
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _code_hash(code: str) -> str:
        return sha256(code.strip().encode("utf-8")).hexdigest()

    def register(self, now: float) -> tuple[int, str]:
        """Reserve the pilot slot and return the participant ID and private access code."""
        code = secrets.token_urlsafe(24)
        with self._connect(write=True) as connection:
            if connection.execute("SELECT 1 FROM participants LIMIT 1").fetchone():
                raise PilotAlreadyRegistered("A pilot participant has already registered.")
            result = connection.execute(
                "INSERT INTO participants (code_hash, registered_at) VALUES (?, ?)",
                (self._code_hash(code), now),
            )
            return result.lastrowid, code

    def participant_id(self, code: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM participants WHERE code_hash = ?", (self._code_hash(code),)
            ).fetchone()
        return row["id"] if row else None

    def participant(self, participant_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, registered_at FROM participants WHERE id = ?", (participant_id,)
            ).fetchone()
        return dict(row) if row else None

    def has_participant(self, participant_id: int) -> bool:
        return self.participant(participant_id) is not None

    def delete_participant(self, code: str) -> bool:
        """Remove participant records in one transaction and leave global spend intact."""
        with self._connect(write=True) as connection:
            result = connection.execute(
                "DELETE FROM participants WHERE code_hash = ?", (self._code_hash(code),)
            )
            return result.rowcount == 1

    @staticmethod
    def _session(row: sqlite3.Row) -> dict:
        session = dict(row)
        session["topics"] = json.loads(session["topics"])
        return session

    def start_session(
        self,
        participant_id: int,
        local_day: str,
        day_number: int,
        started_at: float,
        topics: list[str],
    ) -> dict:
        """Start at most one session per date and return the original on repeated requests."""
        if date.fromisoformat(local_day).isoformat() != local_day:
            raise ValueError("The session date must be YYYY-MM-DD.")
        with self._connect(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE participant_id = ? AND local_day = ?",
                (participant_id, local_day),
            ).fetchone()
            if row is None:
                result = connection.execute(
                    """INSERT INTO sessions (participant_id, local_day, day_number, started_at, topics)
                       VALUES (?, ?, ?, ?, ?)""",
                    (participant_id, local_day, day_number, started_at, json.dumps(topics)),
                )
                row = connection.execute(
                    "SELECT * FROM sessions WHERE id = ?", (result.lastrowid,)
                ).fetchone()
        return self._session(row)

    def get_session(self, participant_id: int, local_day: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE participant_id = ? AND local_day = ?",
                (participant_id, local_day),
            ).fetchone()
        return self._session(row) if row else None

    def sessions(self, participant_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sessions WHERE participant_id = ? ORDER BY day_number",
                (participant_id,),
            ).fetchall()
        return [self._session(row) for row in rows]

    def messages(self, session_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT role, content, at FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_message(self, session_id: int, role: str, text: str, at: float) -> None:
        with self._connect(write=True) as connection:
            result = connection.execute(
                """INSERT INTO messages (session_id, role, content, at)
                   SELECT id, ?, ?, ? FROM sessions WHERE id = ? AND phase = 'chat'""",
                (role, text, at, session_id),
            )
            if not result.rowcount:
                raise ValueError("This session is no longer accepting messages.")

    def transition(self, session_id: int, expected: str, phase: str) -> bool:
        if (expected, phase) not in {("chat", "rating"), ("chat", "crisis"), ("readers", "done")}:
            raise ValueError("Invalid session phase transition.")
        with self._connect(write=True) as connection:
            result = connection.execute(
                "UPDATE sessions SET phase = ? WHERE id = ? AND phase = ?",
                (phase, session_id, expected),
            )
            return result.rowcount == 1

    def save_rating(
        self, session_id: int, rating: int, satisfaction: int | None, at: float
    ) -> bool:
        """Record the local rating only once before the readers run."""
        with self._connect(write=True) as connection:
            result = connection.execute(
                """UPDATE sessions SET rating = ?, satisfaction = ?, rated_at = ?, phase = 'readers'
                   WHERE id = ? AND phase = 'rating'""",
                (rating, satisfaction, at, session_id),
            )
            return result.rowcount == 1

    def save_guess(
        self,
        session_id: int,
        mode: str,
        score: float | None = None,
        confidence: float | None = None,
        cues: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        """Store one reader result per mode. A successful result survives retries."""
        with self._connect(write=True) as connection:
            connection.execute(
                """INSERT INTO guesses (session_id, mode, score, confidence, cues, error)
                   SELECT id, ?, ?, ?, ?, ? FROM sessions WHERE id = ? AND phase = 'readers'
                   ON CONFLICT (session_id, mode) DO UPDATE SET
                       score = excluded.score,
                       confidence = excluded.confidence,
                       cues = excluded.cues,
                       error = excluded.error
                   WHERE guesses.error IS NOT NULL""",
                (mode, score, confidence, json.dumps(cues or []), error, session_id),
            )

    def guesses(self, session_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT mode, score, confidence, cues, error FROM guesses WHERE session_id = ? ORDER BY mode",
                (session_id,),
            ).fetchall()
        return [dict(row) | {"cues": json.loads(row["cues"])} for row in rows]

    def earlier_transcripts(self, participant_id: int, before_day_number: int) -> list[list[dict]]:
        """Return completed prior transcripts as role/content pairs for the history reader."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT sessions.day_number, messages.role, messages.content
                   FROM sessions JOIN messages ON messages.session_id = sessions.id
                   WHERE participant_id = ? AND day_number < ? AND phase = 'done'
                   ORDER BY day_number, messages.id""",
                (participant_id, before_day_number),
            ).fetchall()
        transcripts = {}
        for row in rows:
            transcripts.setdefault(row["day_number"], []).append(
                {"role": row["role"], "content": row["content"]}
            )
        return list(transcripts.values())
