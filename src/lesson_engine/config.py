"""Where things live. Nothing about any person or project is built in.

The lesson store is a directory holding LESSONS.md, found in this order:
  1. $LESSONS_DIR
  2. ~/.config/lesson-engine/lessons-dir   (written by `lesson-engine setup <dir>`)
  3. /etc/lesson-engine/lessons-dir        (a shared machine's default)
The file forms exist because tools don't always inherit your shell's environment.

Which repos to mine is per machine, because session transcripts are per machine.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SYSTEM_FILE = Path(os.environ.get("LESSON_ENGINE_SYSTEM_FILE", "/etc/lesson-engine/lessons-dir"))


def config_dir() -> Path:
    return Path(os.environ.get("LESSON_ENGINE_CONFIG_DIR", Path.home() / ".config" / "lesson-engine"))


def lessons_dir(required: bool = True) -> Path | None:
    candidates = [os.environ.get("LESSONS_DIR", "")]
    for f in (config_dir() / "lessons-dir", SYSTEM_FILE):
        if f.exists():
            candidates.append(f.read_text().strip())
    for c in candidates:
        if c:
            return Path(c).expanduser()
    if required:
        sys.exit("lesson-engine: no lesson store set. Set LESSONS_DIR, or run: lesson-engine setup <dir>")
    return None


def lessons_file() -> Path:
    return lessons_dir() / "LESSONS.md"


def mined_dir() -> Path:
    return lessons_dir() / "mined"


def inbox_dir() -> Path:
    return lessons_dir() / "inbox"


def can_review() -> bool:
    """Only someone who can write the list reviews and promotes; everyone else proposes."""
    return os.access(lessons_file(), os.W_OK)


def targets_file() -> Path:
    return config_dir() / "targets.txt"


def targets() -> list[str]:
    f = targets_file()
    return [l.strip() for l in f.read_text().splitlines() if l.strip()] if f.exists() else []


def marker() -> Path:
    return config_dir() / "transcripts-mined"
