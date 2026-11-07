"""On a shared machine: publish the reviewed list to everyone, and collect what they proposed.

`checkout` is a clone of the private lessons repo; `publish` is the folder everyone's sessions read.
Only the list and the inbox are ever in `publish`. mined/ holds excerpts of past sessions, so it stays
in the checkout. The account running this must own `publish`.
"""
from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
from pathlib import Path


def git(checkout: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True)


def run(checkout: Path, publish: Path) -> int:
    pull = git(checkout, "pull", "--rebase", "--autostash", "-q")
    if pull.returncode:
        print(f"lesson-engine sync: pull failed: {pull.stderr.strip()}", file=sys.stderr)
        return 1

    # Rename first: a proposal written while this runs lands in a fresh file, not a lost one.
    inbox = publish / "inbox"
    taken = []
    for f in sorted(inbox.glob("*.md")):
        claimed = f.with_suffix(".syncing")
        f.rename(claimed)
        taken.append(claimed)
    # A run that died before pushing left these behind; pick them up too.
    taken += [f for f in sorted(inbox.glob("*.syncing")) if f not in taken]
    if taken:
        dest = checkout / "inbox"
        dest.mkdir(exist_ok=True)
        for f in taken:
            with open(dest / f"{f.stem}.md", "a") as out:
                out.write(f.read_text())
        git(checkout, "add", "inbox")
        git(checkout, "commit", "-q", "-m", f"inbox: {len(taken)} file(s) from {getpass.getuser()}'s sync")
        push = git(checkout, "push", "-q")
        if push.returncode:
            print(f"lesson-engine sync: push failed, proposals kept: {push.stderr.strip()}", file=sys.stderr)
            git(checkout, "reset", "-q", "--hard", "HEAD~1")
            return 1
        for f in taken:
            f.unlink()

    src, dst = checkout / "LESSONS.md", publish / "LESSONS.md"
    if not dst.exists() or src.read_bytes() != dst.read_bytes():
        tmp = publish / ".LESSONS.md.new"
        shutil.copyfile(src, tmp)
        tmp.chmod(0o644)
        tmp.replace(dst)  # atomic: a session starting now reads the old list or the new one
    print(f"synced: {len(taken)} inbox file(s) collected")
    return 0
