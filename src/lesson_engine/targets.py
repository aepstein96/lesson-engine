"""Add a repo to the engine without changing anything the repo tracks.

Everything `add` creates is excluded through .git/info/exclude, which is local to the clone, so the
repo can still be handed to someone with no trace of the engine or of any lesson in it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import config

COMMANDS = Path(__file__).resolve().parent / "claude" / "commands"
HOOK = "lesson-engine nudge 2>/dev/null || true"


def repo_root(path: str) -> Path:
    r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"lesson-engine: {path} is not a git repo")
    return Path(r.stdout.strip()).resolve()


def exclude(root: Path, entries: list[str]) -> None:
    f = Path(subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"],
                            capture_output=True, text=True).stdout.strip())
    f = f if f.is_absolute() else root / f
    f.parent.mkdir(parents=True, exist_ok=True)
    have = f.read_text().splitlines() if f.exists() else []
    new = [e for e in entries if e not in have]
    if new:
        with open(f, "a") as out:
            out.write("".join(e + "\n" for e in new))


def add_hook(root: Path) -> None:
    f = root / ".claude" / "settings.local.json"
    settings = json.loads(f.read_text()) if f.exists() else {}
    starts = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
    if not any(h.get("command") == HOOK for g in starts for h in g.get("hooks", [])):
        starts.append({"matcher": "", "hooks": [{"type": "command", "command": HOOK}]})
    f.write_text(json.dumps(settings, indent=2) + "\n")


def remove_hook(root: Path) -> None:
    f = root / ".claude" / "settings.local.json"
    if not f.exists():
        return
    settings = json.loads(f.read_text())
    starts = settings.get("hooks", {}).get("SessionStart", [])
    settings.get("hooks", {})["SessionStart"] = [
        g for g in starts if not any(h.get("command") == HOOK for h in g.get("hooks", []))]
    if not settings["hooks"]["SessionStart"]:
        del settings["hooks"]["SessionStart"]
    if not settings.get("hooks"):
        settings.pop("hooks", None)
    if settings:
        f.write_text(json.dumps(settings, indent=2) + "\n")
    else:
        f.unlink()


def add(path: str) -> int:
    root = repo_root(path)
    lessons = config.lessons_file()
    if not lessons.exists():
        sys.exit(f"lesson-engine: no list at {lessons}")
    # Claude Code loads a gitignored CLAUDE.local.md without asking. An @import of a path outside
    # the project needs interactive approval, and without it a non-interactive session runs with
    # no lessons and no error (measured with `claude -p` and a negative control).
    link = root / "CLAUDE.local.md"
    if link.exists() and not link.is_symlink():
        sys.exit(f"lesson-engine: {link} is a real file; move it aside first")
    link.unlink(missing_ok=True)
    link.symlink_to(lessons.resolve())

    commands = root / ".claude" / "commands"
    commands.mkdir(parents=True, exist_ok=True)
    names = sorted(p.name for p in COMMANDS.glob("*.md"))
    for name in names:
        dest = commands / name
        if dest.exists() and not dest.is_symlink():
            sys.exit(f"lesson-engine: {dest} is a real file; remove it from the repo first")
        dest.unlink(missing_ok=True)
        dest.symlink_to(COMMANDS / name)
    add_hook(root)
    exclude(root, ["/CLAUDE.local.md", "/.claude/settings.local.json"]
            + [f"/.claude/commands/{n}" for n in names])

    config.config_dir().mkdir(parents=True, exist_ok=True)
    have = config.targets()
    if str(root) not in have:
        with open(config.targets_file(), "a") as f:
            f.write(f"{root}\n")
    print(f"added {root}\n  lessons: CLAUDE.local.md -> {lessons}\n  commands: {', '.join(names)}\n"
          "  nothing the repo tracks changed")
    return 0


def remove(path: str) -> int:
    root = repo_root(path)
    keep = [t for t in config.targets() if t != str(root)]
    config.config_dir().mkdir(parents=True, exist_ok=True)
    config.targets_file().write_text("".join(t + "\n" for t in keep))
    link = root / "CLAUDE.local.md"
    if link.is_symlink():
        link.unlink()
    for p in COMMANDS.glob("*.md"):
        dest = root / ".claude" / "commands" / p.name
        if dest.is_symlink():
            dest.unlink()
    remove_hook(root)
    print(f"removed {root}")
    return 0
