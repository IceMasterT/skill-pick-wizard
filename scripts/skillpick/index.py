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

_WORD = re.compile(r"[^\W_]+")
_RRF_K = 60
# Bump when the table layout changes; a mismatch drops and recreates the (cache-only) index.
_SCHEMA_VERSION = 2
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

    Only runs of Unicode letters/digits survive, so no FTS5 syntax can leak through, and quoting
    makes bare operators such as AND/OR/NOT/NEAR plain phrases.
    """
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return None
    return " OR ".join(f'"{w}"' for w in words)


def _invoke_text(invoke: str) -> str:
    return re.sub(r"[-:_]+", " ", invoke)


def _row_to_skill(row: tuple) -> Skill:
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
            # WAL lets readers and the rebuild writer proceed without blocking each other.
            conn.execute("PRAGMA journal_mode=WAL")
            self._ensure_schema(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION:
            return
        # Missing or stale layout: the index is a rebuildable cache, so start clean.
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION:
                for table in ("fts", "skills", "meta"):
                    conn.execute(f"DROP TABLE IF EXISTS {table}")
                self._create_schema(conn)
                conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

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
                self._create_schema(conn)
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
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass  # never mask the original exception
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
        """Reciprocal-rank-fuse the per-query FTS results.

        `allow` is applied to each query's full match list before that query's ranks
        are assigned, so restrictive filters still fill `limit`. All queries run inside
        one read transaction, so a concurrent rebuild can never produce a mixed result.
        """
        scores: dict[str, float] = {}
        matched: dict[str, list[str]] = {}
        found: dict[str, Skill] = {}
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            try:
                for query in dict.fromkeys(queries):
                    fts_query = build_fts_query(query)
                    if fts_query is None:
                        continue
                    rows = conn.execute(
                        "SELECT " + ", ".join(f"s.{c.strip()}" for c in _COLUMNS.split(","))
                        + " FROM fts JOIN skills s ON s.rowid = fts.rowid WHERE fts MATCH ? "
                        "ORDER BY bm25(fts, 10.0, 8.0, 1.0, 3.0), fts.rowid",
                        (fts_query,),
                    ).fetchall()
                    rank = 0
                    for row in rows:
                        skill = _row_to_skill(row)
                        if not allow(skill):
                            continue
                        found[skill.invoke] = skill
                        scores[skill.invoke] = scores.get(skill.invoke, 0.0) + 1.0 / (_RRF_K + rank)
                        matched.setdefault(skill.invoke, []).append(query)
                        rank += 1
            finally:
                conn.execute("COMMIT")
        hits = [Hit(found[k], scores[k], tuple(matched[k])) for k in scores]
        hits.sort(key=lambda h: (-h.score, h.skill.invoke))
        return hits[:limit]
