#!/usr/bin/env python3
"""skill-pick-wizard CLI: search skills, save and show plans, toggle modes. Output is plain text for Claude."""
import sys

if sys.version_info < (3, 10):
    sys.stderr.write("skill-pick-wizard needs Python 3.10 or newer\n")
    sys.exit(2)

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillpick import catalog, service  # noqa: E402
from skillpick.paths import claude_home, data_dir  # noqa: E402
from skillpick.state import (  # noqa: E402
    MODES,
    SessionState,
    Stage,
    clear_session,
    load_config,
    load_session,
    mark_skipped,
    save_config,
    save_plan,
    valid_session_id,
)


class UserError(Exception):
    """An expected failure: reported as one stderr line with exit code 2."""


def _session_id(args, required: bool = True) -> str:
    sid = args.session or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not sid:
        if required:
            raise UserError("no session id")
        return ""
    if not valid_session_id(sid):
        raise UserError("invalid session id")
    return sid


def _cwd(args) -> Path:
    if not args.cwd:
        return Path.cwd()
    path = Path(args.cwd).expanduser()
    if not path.is_dir():
        raise UserError(f"no such directory: {args.cwd}")
    return path


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer: {text!r}")
    return value


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        """One stderr line and exit 2, instead of argparse's usage dump."""
        self.exit(2, f"{self.prog}: error: {message}\n")


def _plan_lines(state: SessionState) -> list[str]:
    return [f"{st.stage}: {', '.join(st.skills)}" for st in state.stages]


def _on_off(value: bool) -> str:
    return "on" if value else "off"


def cmd_status(args) -> None:
    data = data_dir()
    cfg = load_config(data)
    print(f"mode: {cfg.mode}")
    print(f"deep_scan: {_on_off(cfg.deep_scan)}")
    print(f"deep_scan_model: {cfg.deep_scan_model}")
    sid = _session_id(args, required=False)
    state = load_session(data, sid) if sid else None
    print(f"session: {state.status if state else 'none'}")
    if state and state.status == "planned":
        if state.job:
            print(f"job: {state.job}")
        print(f"plan mode: {state.mode or cfg.mode}")
        for line in _plan_lines(state):
            print(line)


def cmd_search(args) -> None:
    cwd = _cwd(args)
    data = data_dir()
    hits = service.search(claude_home(), cwd, data, load_config(data), args.queries, args.limit)
    if not hits:
        print("no matches")
        return
    for hit in hits:
        s = hit.skill
        print(f"`{s.invoke}`  [{', '.join(s.tags)}]  (matched: {'; '.join(hit.queries)})")
        print(f"    {s.description}")


def _parse_plan(raw: str) -> list[Stage]:
    try:
        plan = json.loads(raw)
    except (ValueError, RecursionError):
        raise UserError("invalid --plan JSON") from None
    if not isinstance(plan, list) or not plan:
        raise UserError("invalid --plan: expected a non-empty JSON list")
    shape = 'invalid --plan: each item needs a non-empty "stage" (string) and a non-empty "skills" (list of strings)'
    stages = []
    for item in plan:
        if not (
            isinstance(item, dict)
            and isinstance(item.get("stage"), str)
            and isinstance(item.get("skills"), list)
            and item["stage"].strip()
            and item["skills"]
            and all(isinstance(s, str) and s for s in item["skills"])
        ):
            raise UserError(shape)
        stages.append(Stage(item["stage"], list(item["skills"])))
    return stages


def cmd_save_plan(args) -> None:
    sid = _session_id(args)
    stages = _parse_plan(args.plan)
    cwd = _cwd(args)
    data = data_dir()
    cfg = load_config(data)
    known = service.known_invokes(claude_home(), cwd, data, cfg)
    unknown = list(dict.fromkeys(s for st in stages for s in st.skills if s not in known))
    if unknown:
        raise UserError(f"unknown skills: {', '.join(unknown)}")
    save_plan(data, sid, args.job, stages, args.mode or cfg.mode, args.deep_scan)
    print(f"saved plan: {len(stages)} stages")


def cmd_skip(args) -> None:
    mark_skipped(data_dir(), _session_id(args))
    print("skipped")


def cmd_reset(args) -> None:
    clear_session(data_dir(), _session_id(args))
    print("reset")


def cmd_show_plan(args) -> None:
    state = load_session(data_dir(), _session_id(args))
    if state is None or state.status != "planned":
        print("no plan")
        return
    if state.job:
        print(f"job: {state.job}")
    for line in _plan_lines(state):
        print(line)


def cmd_mode(args) -> None:
    data = data_dir()
    cfg = load_config(data)
    if args.value:
        cfg.mode = args.value
        save_config(data, cfg)
    print(f"mode: {cfg.mode}")


def cmd_deep_scan(args) -> None:
    data = data_dir()
    cfg = load_config(data)
    if args.value:
        cfg.deep_scan = args.value == "on"
        save_config(data, cfg)
    print(f"deep_scan: {_on_off(cfg.deep_scan)}")


def cmd_index(args) -> None:
    cwd = _cwd(args)
    data = data_dir()
    n_global, n_project, searchable = service.index_counts(claude_home(), cwd, data, load_config(data))
    print(f"global: {n_global} skills, project: {n_project} skills")
    print(f"searchable: {searchable}")


def cmd_catalog(args) -> None:
    sid = _session_id(args)
    cwd = _cwd(args)
    data = data_dir()
    skills = service.allowed_skills(claude_home(), cwd, data, load_config(data))
    paths = catalog.write_catalog(skills, data / "catalog" / sid, args.chunks)
    if not paths:
        raise UserError("no skills to catalog")
    for path in paths:
        print(path.resolve())


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="pick.py", description="skill-pick-wizard CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, session: bool = False, cwd: bool = False) -> argparse.ArgumentParser:
        p = sub.add_parser(name)
        p.set_defaults(func=func)
        if session:
            p.add_argument("--session", help="session id (default: $CLAUDE_CODE_SESSION_ID)")
        if cwd:
            p.add_argument("--cwd", help="project directory (default: current directory)")
        return p

    add("status", cmd_status, session=True)
    p = add("search", cmd_search, cwd=True)
    p.add_argument("--limit", type=_positive_int,
                   help="number of candidates (default: 30, or 5 per query when there are more than 6)")
    p.add_argument("queries", nargs="+", metavar="QUERY")
    p = add("save-plan", cmd_save_plan, session=True, cwd=True)
    p.add_argument("--job", required=True)
    p.add_argument("--plan", required=True, help='JSON list: [{"stage": "Planning", "skills": ["brainstorming"]}]')
    p.add_argument("--mode", choices=MODES)
    p.add_argument("--deep-scan", action="store_true")
    add("skip", cmd_skip, session=True)
    add("reset", cmd_reset, session=True)
    add("show-plan", cmd_show_plan, session=True)
    p = add("mode", cmd_mode)
    p.add_argument("value", nargs="?", choices=MODES)
    p = add("deep-scan", cmd_deep_scan)
    p.add_argument("value", nargs="?", choices=("on", "off"))
    add("index", cmd_index, cwd=True)
    p = add("catalog", cmd_catalog, session=True, cwd=True)
    p.add_argument("--chunks", type=_positive_int, default=3)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    try:
        args.func(args)
    except UserError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
