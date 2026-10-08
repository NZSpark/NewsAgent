"""Vector search (TASK-044).

Keeps SQLite as the store. Embeddings default to a dependency-free hashing
vectorizer (char n-grams + token hashing); the Embedder interface lets a real
model be plugged in later without touching callers.

Vectors are stored as JSON in `embeddings(item_type, item_id, dim, vector)`.
Cosine similarity is computed in Python over recent candidates.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from array import array

from ..logging_setup import StorageError, get_logger

log = get_logger("storage.vector")

DIM = 512


def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text)
    grams = [text[i : i + 3] for i in range(len(text) - 2)]
    return words + grams


def embed(text: str, dim: int = DIM) -> list[float]:
    """Deterministic hashing embedding (no external deps)."""
    vec = [0.0] * dim
    for token in _tokenize(text):
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 16) & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def init_vector_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS embeddings (
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (item_type, item_id)
        )"""
    )
    conn.commit()


def upsert_vector(conn: sqlite3.Connection, item_type: str, item_id: str, vector: list[float]) -> None:
    from datetime import datetime, timezone

    try:
        conn.execute(
            """INSERT INTO embeddings (item_type, item_id, dim, vector, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(item_type, item_id) DO UPDATE SET
                 dim=excluded.dim, vector=excluded.vector, updated_at=excluded.updated_at""",
            (item_type, item_id, len(vector), json.dumps(vector), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise StorageError(f"upsert vector failed: {exc}") from exc


def index_events(conn: sqlite3.Connection, limit: int = 500) -> int:
    """Embed recent events (title + summary)."""
    init_vector_schema(conn)
    rows = list(conn.execute("SELECT event_id, title, summary FROM events LIMIT ?", (limit,)))
    for row in rows:
        text = f"{row['title']}\n{row['summary'] or ''}"
        upsert_vector(conn, "event", row["event_id"], embed(text))
    return len(rows)


def semantic_search(conn: sqlite3.Connection, query: str, limit: int = 20, min_score: float = 0.05) -> list[dict]:
    """Cosine search over stored event vectors, joined with event rows."""
    init_vector_schema(conn)
    qvec = embed(query)
    results: list[tuple[float, dict]] = []
    rows = list(
        conn.execute(
            """SELECT e.*, v.vector AS _vec FROM embeddings v
               JOIN events e ON e.event_id = v.item_id
               WHERE v.item_type='event'"""
        )
    )
    for row in rows:
        data = dict(row)
        vec = json.loads(data.pop("_vec"))
        score = cosine(qvec, vec)
        if score >= min_score:
            data["score"] = round(score, 4)
            results.append((score, data))
    results.sort(key=lambda x: x[0], reverse=True)
    return [data for _, data in results[:limit]]
