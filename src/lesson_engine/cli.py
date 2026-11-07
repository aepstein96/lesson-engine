"""lesson-engine: mine your Claude Code sessions for lessons, and load them into future sessions."""
from __future__ import annotations

import argparse
import getpass
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config, targets


def passthrough(module: str, args: list[str], prog: str) -> int:
    """The pipeline scripts parse their own arguments."""
    mod = __import__(f"lesson_engine.{module}", fromlist=["main"])
    sys.argv = [prog, *args]
    if "--test" in args:
        return mod.run_tests()
    return mod.main() if module in ("mine_transcripts", "survey_transcripts") else mod.main(args)


def cmd_setup(a) -> int:
    d = Path(a.dir).expanduser().resolve()
    if not (d / "LESSONS.md").exists():
        sys.exit(f"lesson-engine: no LESSONS.md in {d}")
    config.config_dir().mkdir(parents=True, exist_ok=True)
    (config.config_dir() / "lessons-dir").write_text(f"{d}\n")
    print(f"lesson store: {d}\n(the LESSONS_DIR environment variable overrides this)")
    return 0


def cmd_list(a) -> int:
    print(f"lesson store: {config.lessons_dir(required=False) or '(not set)'}")
    for t in config.targets():
        print(f"  {t}")
    return 0


def cmd_lesson(a) -> int:
    """Save a proposed lesson. No session loads the inbox, so it can't steer anyone until reviewed."""
    text = " ".join(a.text).strip()
    if not text:
        sys.exit('usage: lesson-engine lesson "<the lesson, and the evidence for it>"')
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    where = Path(r.stdout.strip()).name if r.returncode == 0 else Path.cwd().name
    user = getpass.getuser()
    inbox = config.inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(inbox / f"{user}.md", "a") as f:
        f.write(f"## {stamp} | {user} | {where}\n\n{text}\n\n")
    print(f"saved to {inbox / f'{user}.md'}")
    return 0


def cmd_nudge(a) -> int:
    """SessionStart hook. Only informs, and every failure stays silent: a hook that can't resolve
    its own inputs must not be able to derail a session."""
    from . import mine_transcripts
    try:
        payload = sys.stdin.read() if not sys.stdin.isatty() else ""
        if '"source":"compact"' in payload.replace(" ", ""):
            return 0
        import re
        m = re.search(r'"session_id"\s*:\s*"([^"]*)"', payload)
        n = len(mine_transcripts.unmined(exclude=m.group(1) if m else ""))
    except Exception:
        return 0
    if n > 0:
        print(f"{n} session transcript(s) have not been mined for lessons. Mining reads past sessions "
              "for mistakes nobody wrote down, in the background, without blocking this session: "
              "dispatch /mine-transcripts when convenient.")
    return 0


def cmd_paths(a) -> int:
    """What a command file needs to know, in one call."""
    print(f"LESSONS_DIR={config.lessons_dir()}\nLESSONS={config.lessons_file()}\nMINED={config.mined_dir()}\n"
          f"INBOX={config.inbox_dir()}\nCAN_REVIEW={'yes' if config.can_review() else 'no'}")
    return 0


def cmd_mark_mined(a) -> int:
    config.config_dir().mkdir(parents=True, exist_ok=True)
    config.marker().touch()
    print("sessions up to now are marked mined")
    return 0


def cmd_test(a) -> int:
    failed = 0
    for m in ("mine_transcripts", "survey_transcripts", "lesson_ledger", "promote_lessons",
              "promote_checks", "prune_lessons"):
        failed += bool(__import__(f"lesson_engine.{m}", fromlist=["run_tests"]).run_tests())
    print("all self-tests pass" if not failed else f"{failed} module(s) failed")
    return 1 if failed else 0


def cmd_sync(a) -> int:
    from . import sync
    return sync.run(Path(a.checkout).expanduser().resolve(), Path(a.publish).expanduser().resolve())


PIPELINE = {"digest": "mine_transcripts", "survey": "survey_transcripts", "ledger": "lesson_ledger",
            "promote": "promote_lessons", "promote-checks": "promote_checks", "prune": "prune_lessons"}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in PIPELINE:
        return passthrough(PIPELINE[sys.argv[1]], sys.argv[2:], f"lesson-engine {sys.argv[1]}")
    p = argparse.ArgumentParser(prog="lesson-engine", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")
    s = sub.add_parser("setup", help="set the lesson store on this machine"); s.add_argument("dir"); s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("add", help="add a repo: load lessons there, install the commands, mine its sessions")
    s.add_argument("path", nargs="?", default="."); s.set_defaults(fn=lambda a: targets.add(a.path))
    s = sub.add_parser("remove", help="undo add"); s.add_argument("path", nargs="?", default=".")
    s.set_defaults(fn=lambda a: targets.remove(a.path))
    s = sub.add_parser("list", help="show the store and the repos"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("lesson", help="save a proposed lesson to the inbox"); s.add_argument("text", nargs="*"); s.set_defaults(fn=cmd_lesson)
    s = sub.add_parser("paths", help="print the store's locations"); s.set_defaults(fn=cmd_paths)
    s = sub.add_parser("nudge", help="session-start hook"); s.set_defaults(fn=cmd_nudge)
    s = sub.add_parser("mark-mined", help="record that sessions up to now have been mined"); s.set_defaults(fn=cmd_mark_mined)
    s = sub.add_parser("test", help="run every self-test"); s.set_defaults(fn=cmd_test)
    s = sub.add_parser("sync", help="shared machine: publish the list, collect the inbox")
    s.add_argument("--checkout", required=True); s.add_argument("--publish", required=True); s.set_defaults(fn=cmd_sync)
    for name in PIPELINE:
        sub.add_parser(name, help=f"pipeline step (see lesson-engine {name} --help)")
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
