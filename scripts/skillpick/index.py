"""SQLite FTS5 index of skills with multi-query reciprocal-rank-fusion search.

One database holds every segment ("global", "project:<key>", ...) so a search ranks one corpus.
Each segment is replaced independently and carries its own fingerprint.
"""
import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .sources import Skill

_WORD = re.compile(r"[^\W_]+")
_RRF_K = 60
# Bump when the table layout changes; a mismatch drops and recreates the (cache-only) index.
_SCHEMA_VERSION = 3
_COLUMNS = "invoke, name, description, source, plugin_id, path, tags_json"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS skills(
    rowid INTEGER PRIMARY KEY,
    segment TEXT NOT NULL,
    invoke TEXT NOT NULL,
    name TEXT,
    description TEXT,
    source TEXT,
    plugin_id TEXT,
    path TEXT,
    tags_json TEXT,
    UNIQUE(segment, invoke)
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


def _fp_key(segment: str) -> str:
    return f"fingerprint:{segment}"


def _path_key(segment: str) -> str:
    return f"path:{segment}"


def _used_key(segment: str) -> str:
    return f"used:{segment}"


# Rewriting last-used on every search would turn reads into writes; once an hour is plenty for a 30-day expiry.
_TOUCH_INTERVAL = 3600.0


def _write_usage(conn: sqlite3.Connection, segment: str, path: str, now: float) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)", (_path_key(segment), path))
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)", (_used_key(segment), repr(now)))


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

    def stored_fingerprint(self, segment: str = "global") -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT v FROM meta WHERE k = ?", (_fp_key(segment),)).fetchone()
        return row[0] if row else None

    def replace_segment(
        self, segment: str, skills: list[Skill], fp: str, path: str | None = None, now: float | None = None
    ) -> None:
        """Replace one segment's rows and fingerprint in a single transaction; other segments are untouched.

        With `path`, the segment's project path and last-used time (`now`, default the current time)
        are recorded in the same transaction, so a project segment never exists without them.
        """
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "DELETE FROM fts WHERE rowid IN (SELECT rowid FROM skills WHERE segment = ?)",
                    (segment,),
                )
                conn.execute("DELETE FROM skills WHERE segment = ?", (segment,))
                seen: set[str] = set()
                for s in skills:
                    if s.invoke in seen:
                        continue
                    seen.add(s.invoke)
                    cur = conn.execute(
                        f"INSERT INTO skills(segment, {_COLUMNS}) VALUES (?,?,?,?,?,?,?,?)",
                        (segment, s.invoke, s.name, s.description, s.source, s.plugin_id, s.path,
                         json.dumps(list(s.tags))),
                    )
                    conn.execute(
                        "INSERT INTO fts(rowid, invoke, name, description, tags) VALUES (?,?,?,?,?)",
                        (cur.lastrowid, _invoke_text(s.invoke), s.name, s.description,
                         " ".join(s.tags)),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)", (_fp_key(segment), fp)
                )
                if path is not None:
                    _write_usage(conn, segment, path, time.time() if now is None else now)
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass  # never mask the original exception
                raise

    def touch_segment(self, segment: str, path: str, now: float) -> None:
        """Record the segment's project path and last-used time (a write only when stale or changed)."""
        with closing(self._connect()) as conn:
            rows = dict(conn.execute(
                "SELECT k, v FROM meta WHERE k IN (?, ?)", (_path_key(segment), _used_key(segment))
            ).fetchall())
            try:
                fresh = (
                    rows.get(_path_key(segment)) == path
                    and 0 <= now - float(rows[_used_key(segment)]) < _TOUCH_INTERVAL
                )
            except (KeyError, ValueError):
                fresh = False
            if fresh:
                return
            conn.execute("BEGIN IMMEDIATE")
            try:
                _write_usage(conn, segment, path, now)
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    @staticmethod
    def _prune_candidates(
        conn: sqlite3.Connection, keep: str, cutoff: float, path_exists: Callable[[str], bool],
        only: set[str] | None = None,
    ) -> list[str]:
        meta = dict(conn.execute("SELECT k, v FROM meta WHERE k LIKE 'used:project:%' OR k LIKE 'path:project:%'"))
        segments = {k[len("used:"):] for k in meta if k.startswith("used:")}
        segments |= {r[0] for r in conn.execute("SELECT DISTINCT segment FROM skills WHERE segment LIKE 'project:%'")}
        out = []
        for segment in sorted(segments):
            if segment == keep or (only is not None and segment not in only):
                continue
            used, path = meta.get(_used_key(segment)), meta.get(_path_key(segment))
            try:
                stale = used is None or float(used) < cutoff
            except ValueError:
                stale = True
            if stale or path is None or not path_exists(path):
                out.append(segment)
        return out

    def prune_segments(
        self, keep: str, now: float, max_age_days: float, path_exists: Callable[[str], bool]
    ) -> list[str]:
        """Drop project segments whose recorded path is gone, that were unused for max_age_days,
        or that have no usage metadata. The "global" segment and `keep` are never dropped.

        Candidates are selected (including the path checks) in a read snapshot; a write
        transaction is opened only when there is something to drop, and the candidates are
        re-verified inside it. Returns the dropped segment names.
        """
        cutoff = now - max_age_days * 86400
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            try:
                candidates = self._prune_candidates(conn, keep, cutoff, path_exists)
            finally:
                conn.execute("COMMIT")
            if not candidates:
                return []
            conn.execute("BEGIN IMMEDIATE")
            try:
                dropped = self._prune_candidates(conn, keep, cutoff, path_exists, set(candidates))
                for segment in dropped:
                    conn.execute(
                        "DELETE FROM fts WHERE rowid IN (SELECT rowid FROM skills WHERE segment = ?)",
                        (segment,),
                    )
                    conn.execute("DELETE FROM skills WHERE segment = ?", (segment,))
                    conn.execute(
                        "DELETE FROM meta WHERE k IN (?, ?, ?)",
                        (_fp_key(segment), _path_key(segment), _used_key(segment)),
                    )
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        return dropped

    def count(self, segment: str | None = None) -> int:
        where, params = ("WHERE segment = ?", (segment,)) if segment is not None else ("", ())
        with closing(self._connect()) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM skills {where}", params).fetchone()[0]

    def all(self, segment: str | None = None) -> list[Skill]:
        """Skills of one segment, or of every segment when segment is None."""
        where, params = ("WHERE segment = ?", (segment,)) if segment is not None else ("", ())
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM skills {where} ORDER BY invoke, segment", params
            ).fetchall()
        return [_row_to_skill(r) for r in rows]

    def get(self, invoke: str, segment: str = "global") -> Skill | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM skills WHERE segment = ? AND invoke = ?", (segment, invoke)
            ).fetchone()
        return _row_to_skill(row) if row else None

    @staticmethod
    def _shadowed(conn, allow, override_segment: str | None) -> set[str]:
        if override_segment is None:
            return set()
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM skills WHERE segment = ?", (override_segment,)
        ).fetchall()
        return {r[0] for r in rows if allow(_row_to_skill(r), override_segment)}

    def visible(
        self,
        allow: Callable[[Skill, str], bool] = lambda skill, segment: True,
        override_segment: str | None = None,
    ) -> list[Skill]:
        """Every skill passing `allow`, sorted by invoke. Rows of other segments whose invoke is
        also (allowed) in `override_segment` are shadowed. One read snapshot."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            try:
                shadowed = self._shadowed(conn, allow, override_segment)
                rows = conn.execute(f"SELECT {_COLUMNS}, segment FROM skills").fetchall()
            finally:
                conn.execute("COMMIT")
        skills = [
            _row_to_skill(r[:-1]) for r in rows
            if allow(_row_to_skill(r[:-1]), r[-1])
            and (r[-1] == override_segment or r[0] not in shadowed)
        ]
        return sorted(skills, key=lambda s: s.invoke)

    def search(
        self,
        queries: list[str],
        limit: int = 30,
        allow: Callable[[Skill, str], bool] = lambda skill, segment: True,
        override_segment: str | None = None,
    ) -> list[Hit]:
        """Reciprocal-rank-fuse the per-query FTS results.

        `allow(skill, segment)` is applied to each query's full match list before that query's ranks
        are assigned, so restrictive filters still fill `limit`. A skill in another segment whose
        invoke is also allowed in `override_segment` is shadowed by it. All queries and the shadow
        lookup run inside one read transaction, so a concurrent rebuild can never produce a mixed
        result. Candidates are keyed by (segment, invoke): the same invoke in two segments stays two hits.
        """
        scores: dict[tuple[str, str], float] = {}
        matched: dict[tuple[str, str], list[str]] = {}
        found: dict[tuple[str, str], Skill] = {}
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            try:
                shadowed = self._shadowed(conn, allow, override_segment)
                for query in dict.fromkeys(queries):
                    fts_query = build_fts_query(query)
                    if fts_query is None:
                        continue
                    rows = conn.execute(
                        "SELECT " + ", ".join(f"s.{c.strip()}" for c in _COLUMNS.split(",")) + ", s.segment"
                        + " FROM fts JOIN skills s ON s.rowid = fts.rowid WHERE fts MATCH ? "
                        "ORDER BY bm25(fts, 10.0, 8.0, 1.0, 3.0), fts.rowid",
                        (fts_query,),
                    ).fetchall()
                    rank = 0
                    for row in rows:
                        skill, segment = _row_to_skill(row[:-1]), row[-1]
                        if not allow(skill, segment):
                            continue
                        if segment != override_segment and skill.invoke in shadowed:
                            continue
                        key = (segment, skill.invoke)
                        found[key] = skill
                        scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
                        matched.setdefault(key, []).append(query)
                        rank += 1
            finally:
                conn.execute("COMMIT")
        hits = [Hit(found[k], scores[k], tuple(matched[k])) for k in scores]
        hits.sort(key=lambda h: (-h.score, h.skill.invoke))
        return hits[:limit]
