"""Minimal line-based parser for the YAML subset used in SKILL.md frontmatter."""
import re

_KEY = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*):(?:[ \t]+(.*))?$")
_NESTED = re.compile(r"^[^\s:][^:]*:(?:\s|$)")
_BLOCK_INDICATORS = {">", ">-", ">+", "|", "|-", "|+"}


def parse_frontmatter(text: str) -> dict:
    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].rstrip() != "---":
        return {}
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip() == "---")
    except StopIteration:
        return {}

    result: dict = {}
    key = None
    value = ""
    cont: list = []

    def flush():
        if key is None:
            return
        parsed = _finish(value, cont)
        if parsed is not None:
            result[key] = parsed

    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = None if line[0] in " \t" else _KEY.match(line)
        if match:
            flush()
            key, value, cont = match.group(1), (match.group(2) or "").strip(), []
        elif key is not None and (line[0] in " \t" or line.startswith("- ") or line.rstrip() == "-"):
            cont.append(line.strip())
    flush()
    return result


def _finish(value: str, cont: list):
    if value in _BLOCK_INDICATORS:
        return " ".join(cont)
    if value:
        if value.startswith("[") and not cont:
            return _inline_list(value)
        return _unquote(" ".join([value] + cont))
    if not cont:
        return ""
    if all(c == "-" or c.startswith("- ") for c in cont):
        return [_unquote(c[1:].strip()) for c in cont]
    if _NESTED.match(cont[0]):
        return None  # nested mapping: unsupported, dropped
    return _unquote(" ".join(cont))


def _inline_list(value: str) -> list:
    body = value.strip()
    if body.endswith("]"):
        body = body[:-1]
    body = body[1:]
    items, buf, quote = [], [], None
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))
    return [_unquote(i.strip()) for i in items if i.strip()]


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return re.sub(r'\\(["\\])', r"\1", s[1:-1])
    return s
