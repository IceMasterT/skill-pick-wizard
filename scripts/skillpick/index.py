"""SQLite FTS5 index of skills with multi-query reciprocal-rank-fusion search."""
import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .sources import Skill

_WORD = re.compile(r"[A-Za-z0-9]+")
_RRF_K = 60
_PER_QUERY_LIMIT = 100
_COLUMNS = "invoke, name, description, source, plugin_id, path, tags_json"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS skills(
    rowid INTEGER PRIMARY KEY,
    invoke TEXT UNIQUE,
    name TEXT,
    description TEXT,
    source TEXT,
    plugin_id TEXT,
    path TEXT,
    tags_json TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    invoke, name, description, tags,
    content='skills', content_rowid='rowid',
    tokenize='porter unicode61'
);
"""


@dataclass(frozen=True)
class Hit:
    skill: Skill
    score: float
    queries: tuple[str, ...]


def fingerprint(files: list[Path]) -> str:
    """sha256 over sorted (path, mtime_ns) pairs; a missing file contributes (path, -1)."""
    pairs = []
    for f in files:
        try:
            mtime = Path(f).stat().st_mtime_ns
        except OSError:
            mtime = -1
        pairs.append((str(f), mtime))
    digest = hashlib.sha256()
    for path, mtime in sorted(pairs):
        digest.update(f"{path}\0{mtime}\n".encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


def build_fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query: every word quoted, joined with OR.

    Only [A-Za-z0-9]+ words survive, so no FTS5 syntax can leak through, and quoting
    makes bare operators such as AND/OR/NOT/NEAR plain phrases.
    """
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return None
    return " OR ".join(f'"{w}"' for w in words)


def _invoke_text(invoke: str) -> str:
    return re.sub(r"[-:_]+", " ", invoke)


def _row_to_skill(row) -> Skill:
    invoke, name, description, source, plugin_id, path, tags_json = row
    return Skill(invoke, name, description, source, plugin_id, path, tuple(json.loads(tags_json)))


class SkillIndex:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: transactions are managed explicitly.
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        try:
            conn.executescript(_SCHEMA)
        except BaseException:
            conn.close()
            raise
        return conn

    def stored_fingerprint(self) -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT v FROM meta WHERE k = 'fingerprint'").fetchone()
        return row[0] if row else None

    def rebuild(self, skills: list[Skill], fp: str) -> None:
        """Drop and refill the whole index in a single transaction."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DROP TABLE IF EXISTS fts")
                conn.execute("DROP TABLE IF EXISTS skills")
                conn.execute("DROP TABLE IF EXISTS meta")
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                seen: set[str] = set()
                for s in skills:
                    if s.invoke in seen:
                        continue
                    seen.add(s.invoke)
                    cur = conn.execute(
                        f"INSERT INTO skills({_COLUMNS}) VALUES (?,?,?,?,?,?,?)",
                        (s.invoke, s.name, s.description, s.source, s.plugin_id, s.path,
                         json.dumps(list(s.tags))),
                    )
                    conn.execute(
                        "INSERT INTO fts(rowid, invoke, name, description, tags) VALUES (?,?,?,?,?)",
                        (cur.lastrowid, _invoke_text(s.invoke), s.name, s.description,
                         " ".join(s.tags)),
                    )
                conn.execute("INSERT INTO meta(k, v) VALUES ('fingerprint', ?)", (fp,))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def count(self) -> int:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]

    def all(self) -> list[Skill]:
        with closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM skills ORDER BY invoke").fetchall()
        return [_row_to_skill(r) for r in rows]

    def get(self, invoke: str) -> Skill | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM skills WHERE invoke = ?", (invoke,)
            ).fetchone()
        return _row_to_skill(row) if row else None

    def search(
        self,
        queries: list[str],
        limit: int = 30,
        allow: Callable[[Skill], bool] = lambda s: True,
    ) -> list[Hit]:
        scores: dict[int, float] = {}
        matched: dict[int, list[str]] = {}
        with closing(self._connect()) as conn:
            for query in queries:
                fts_query = build_fts_query(query)
                if fts_query is None:
                    continue
                rows = conn.execute(
                    "SELECT rowid FROM fts WHERE fts MATCH ? "
                    "ORDER BY bm25(fts, 10.0, 8.0, 1.0, 3.0) LIMIT ?",
                    (fts_query, _PER_QUERY_LIMIT),
                ).fetchall()
                for rank, (rowid,) in enumerate(rows):
                    scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (_RRF_K + rank)
                    matched.setdefault(rowid, []).append(query)
            if not scores:
                return []
            skills: dict[int, Skill] = {}
            for rowid in scores:
                row = conn.execute(
                    f"SELECT {_COLUMNS} FROM skills WHERE rowid = ?", (rowid,)
                ).fetchone()
                if row:
                    skills[rowid] = _row_to_skill(row)
        hits = [
            Hit(skills[r], scores[r], tuple(matched[r]))
            for r in scores
            if r in skills and allow(skills[r])
        ]
        hits.sort(key=lambda h: (-h.score, h.skill.invoke))
        return hits[:limit]
