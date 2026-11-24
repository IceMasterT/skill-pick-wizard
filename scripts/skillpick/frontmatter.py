"""Minimal line-based parser for the YAML subset used in SKILL.md frontmatter.

Supported: top-level `key: value` scalars (plain, single-quoted, double-quoted
with the escapes \\n \\t \\r \\0 \\b \\f \\" \\\\ \\/ \\xXX \\uXXXX), `#` comments
(a `#` preceded by whitespace outside quotes), multi-line plain/quoted scalars,
folded/literal block scalars (joined to one line), inline lists (possibly
spanning lines) and `- item` block lists. Nested mappings are dropped.
"""
import re

_KEY = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*):(?:[ \t]+(.*))?$")
_NESTED = re.compile(r"^[^\s:][^:]*:(?:\s|$)")
_BLOCK_INDICATORS = {">", ">-", ">+", "|", "|-", "|+"}
_COMMENT = re.compile(r"(?:^|\s)#.*$")
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "b": "\b", "f": "\f",
                   '"': '"', "\\": "\\", "/": "/"}
_ESCAPE = re.compile(r'\\(?:([ntr0bf"\\/])|x([0-9A-Fa-f]{2})|u([0-9A-Fa-f]{4}))')


def parse_frontmatter(text: str) -> dict[str, str | list[str]]:
    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].rstrip() != "---":
        return {}
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip() == "---")
    except StopIteration:
        return {}

    result: dict[str, str | list[str]] = {}
    key = None
    value = ""
    cont: list[str] = []

    def flush() -> None:
        if key is None:
            return
        parsed = _finish(value, cont)
        if parsed is not None:
            result[key] = parsed

    for line in lines[1:end]:
        if not line.strip():
            continue
        indented = line[0] in " \t"
        in_block = key is not None and _strip_comment(value) in _BLOCK_INDICATORS
        if not indented and line.startswith("#"):
            continue
        if indented and not in_block and line.lstrip().startswith("#"):
            continue
        match = None if indented else _KEY.match(line)
        if match:
            flush()
            key, value, cont = match.group(1), (match.group(2) or "").strip(), []
        elif key is not None and (indented or line.startswith("- ") or line.rstrip() == "-"):
            cont.append(line.strip())
    flush()
    return result


def _finish(value: str, cont: list[str]):
    if not value.startswith(("'", '"')):
        value = _strip_comment(value)
    if value in _BLOCK_INDICATORS:
        return " ".join(cont)
    if value:
        if value.startswith("["):
            return _inline_list(" ".join([value] + cont))
        return _scalar(" ".join([value] + cont), joined=True)
    if not cont:
        return ""
    if all(c == "-" or c.startswith("- ") for c in cont):
        return [_scalar(c[1:]) for c in cont]
    if _NESTED.match(cont[0]):
        return None  # nested mapping: unsupported, dropped
    return _scalar(" ".join(cont), joined=True)


def _strip_comment(s: str) -> str:
    return _COMMENT.sub("", s).strip()


def _scalar(s: str, joined: bool = False) -> str:
    """Unquote a quoted scalar (dropping a trailing comment) or strip comments from a plain one."""
    s = s.strip()
    if s[:1] in ("'", '"'):
        parsed = _read_quoted(s, 0)
        if parsed is not None:
            value, after = parsed
            rest = s[after:].strip()
            if not rest or (rest.startswith("#") and s[after].isspace()):
                return value
    return _strip_comment(s)


def _read_quoted(s: str, i: int):
    """Read a quoted scalar starting at s[i]; return (value, index after closing quote) or None."""
    quote = s[i]
    out = []
    j = i + 1
    while j < len(s):
        ch = s[j]
        if quote == "'":
            if ch == "'":
                if s[j + 1:j + 2] == "'":
                    out.append("'")
                    j += 2
                    continue
                return "".join(out), j + 1
        else:
            if ch == "\\" and j + 1 < len(s):
                m = _ESCAPE.match(s, j)
                if m:
                    simple, hx, uni = m.groups()
                    out.append(_SIMPLE_ESCAPES[simple] if simple else chr(int(hx or uni, 16)))
                    j = m.end()
                else:
                    out.append(s[j:j + 2])
                    j += 2
                continue
            if ch == '"':
                return "".join(out), j + 1
        out.append(ch)
        j += 1
    return None


def _inline_list(s: str) -> list[str]:
    items: list[str] = []
    buf: list[str] = []
    i = s.index("[") + 1
    while i < len(s):
        ch = s[i]
        if ch in "'\"":
            parsed = _read_quoted(s, i)
            if parsed is not None:
                buf.append(parsed[0])
                i = parsed[1]
                continue
        if ch in ",]":
            item = "".join(buf).strip()
            if item:
                items.append(item)
            buf = []
            if ch == "]":
                break
        else:
            buf.append(ch)
        i += 1
    else:
        item = "".join(buf).strip()
        if item:
            items.append(item)
    return items


def tag_list(value) -> tuple[str, ...]:
    """Normalise a parsed `tags` value (list or comma-separated string) to a tuple."""
    items = value.split(",") if isinstance(value, str) else (value or [])
    return tuple(t.strip() for t in items if t.strip())
