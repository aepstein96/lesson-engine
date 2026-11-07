#!/usr/bin/env python3
"""Land `VERDICT: check` proposals as runnable checks, without a human in the loop.

Prose lessons already promote themselves. Checks did not, and checks are the half that actually
prevents recurrence — the escalation ladder prefers them, so autonomy was improving the weaker
channel. This closes that, for the subset where it can be done safely.

THE SAFE SUBSET, and why the line is here. A prose lesson is read; a check RUNS, in every
session, so a bad one breaks work rather than merely misinforming. Three properties make
autonomy defensible, and a proposal failing any of them is refused rather than massaged:

  ADDITIVE   a check lands as a NEW file under scripts/checks/. It never edits existing code,
             so it cannot change what anything already does. An existing filename is refused,
             not overwritten.
  PROVEN     it is executed BEFORE it lands, in a subprocess with a timeout, and kept only if it
             exits 0 against the real repo. A check that cannot pass on a clean tree is a broken
             check, and this is the one class of proposal whose correctness is decidable here.
  BOUNDED    it may read the repo and shell out to read-only git, but the destructive and
             outbound patterns below are refused outright. Assertive checks (verify repo state)
             are in scope; interceptive ones (PreToolUse hooks that gate other commands) are
             NOT — a hook runs before every tool call and a bad one can wedge a session, which
             no pre-flight run can rule out. Hooks stay human-shipped.

Usage:
  lesson-engine promote-checks mined/2026-07-31-collated.md          # dry run
  lesson-engine promote-checks mined/2026-07-31-collated.md --apply

Self-check:  lesson-engine promote-checks --test
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

from . import config
import tempfile

BLOCK_RE = re.compile(r"^```check(?:\s+(\S+))?[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
TIMEOUT = 90

# A check may read the repo and run read-only git. These are refused: they either mutate the
# working tree, reach the network, or write outside the checks directory.
FORBIDDEN = (
    (re.compile(r"\brm\s+-rf|shutil\.rmtree|os\.removedirs"), "recursive delete"),
    # Must match the LIST form too — subprocess.run(["git", "push"]) has no whitespace between
    # the words, so a `git\s+push` pattern misses the way it is actually written in Python.
    (re.compile(r"""\bgit\b["'\s,\]\[]{1,6}(push|commit|reset|checkout|clean|rebase|merge)\b"""),
     "git write"),
    (re.compile(r"\b(urlopen|requests\.|curl|wget|socket\.)"), "network access"),
    (re.compile(r"\bopen\([^)]*['\"][wa]"), "file write"),
    (re.compile(r"os\.environ\[[^]]*\]\s*="), "environment mutation"),
    (re.compile(r"\bsys\.path\.insert\(0,\s*['\"]/"), "absolute sys.path injection"),
)


def parse(md: str) -> list[tuple[str, str]]:
    """[(slug, source)] from ```check <slug> fenced blocks, in document order."""
    out = []
    for m in BLOCK_RE.finditer(md):
        slug, body = m.group(1), m.group(2)
        if not slug:
            first = body.split("\n", 1)[0]
            slug = re.sub(r"[^a-z0-9]+", "-", first.lower()).strip("-")[:40] or "unnamed"
        out.append((slug, body))
    return out


def validate(slug: str, src: str) -> str:
    """'' if the check may be landed, else why not."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,49}", slug or ""):
        return f"bad slug {slug!r} (lowercase, hyphens, 3-50 chars)"
    if "__main__" not in src:
        return "no `if __name__ == \"__main__\"` entry point — nothing to run"
    if len(src) > 8000:
        return f"too long ({len(src)} chars) — a check this size is a program, ship it by hand"
    for pat, why in FORBIDDEN:
        if pat.search(src):
            return f"contains {why}"
    return ""


def run(path: str, cwd: str) -> tuple[bool, str]:
    try:
        p = subprocess.run([sys.executable, path], cwd=cwd, capture_output=True,
                           text=True, timeout=TIMEOUT)
        return p.returncode == 0, (p.stdout + p.stderr).strip().split("\n")[-1][:160]
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT}s"
    except Exception as e:                                    # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__.split("Usage:")[1].strip(), file=sys.stderr)
        return 2
    apply_ = "--apply" in argv
    # A check belongs to the repo it guards, so this runs from inside that repo.
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True).stdout.strip()
    if not root:
        print("run this inside the repo the check should land in", file=sys.stderr)
        return 2
    src_path = args[0]
    if not os.path.isabs(src_path) and not os.path.exists(src_path):
        src_path = str(config.lessons_dir() / src_path)
    if not os.path.exists(src_path):
        print(f"no such collated file: {src_path}", file=sys.stderr)
        return 2
    checks_dir = os.path.join(root, "scripts", "checks")

    blocks = parse(open(src_path).read())
    print(f"{len(blocks)} check block(s) in {os.path.relpath(src_path, root)}")
    landed = 0
    for slug, src in blocks:
        why = validate(slug, src)
        dest = os.path.join(checks_dir, f"{slug}.py")
        if why:
            print(f"  REFUSED  {slug:<34} {why}")
            continue
        if os.path.exists(dest):
            print(f"  SKIP     {slug:<34} already exists (never overwritten)")
            continue
        # Prove it before landing it: run from a temp copy so a failing candidate leaves nothing.
        with tempfile.TemporaryDirectory() as tmp:
            probe = os.path.join(tmp, f"{slug}.py")
            open(probe, "w").write(src)
            ok, last = run(probe, root)
        if not ok:
            print(f"  FAILED   {slug:<34} does not pass on a clean tree: {last}")
            continue
        print(f"  {'LAND' if apply_ else 'WOULD LAND'}     {slug:<34} passes: {last}")
        if apply_:
            os.makedirs(checks_dir, exist_ok=True)
            open(dest, "w").write(src if src.endswith("\n") else src + "\n")
            os.chmod(dest, 0o755)
            landed += 1
    if apply_ and landed:
        print(f"\nlanded {landed} check(s) in scripts/checks/. "
              f"Run them all: python3 scripts/checks/run-all.py")
    elif not apply_:
        print("\ndry run — pass --apply to write. Nothing changed.")
    return 0


def run_tests() -> int:
    bad = 0

    def check(name, got, want):
        nonlocal bad
        if got != want:
            bad += 1
            print(f"FAIL {name}: want {want!r} got {got!r}")

    good = 'import sys\nif __name__ == "__main__":\n    sys.exit(0)\n'
    check("a well-formed check passes validation", validate("no-absolute-paths", good), "")
    check("slug must be kebab", validate("Bad Slug", good)[:8], "bad slug")
    check("needs an entry point", validate("x-y-z", "print(1)\n"),
          'no `if __name__ == "__main__"` entry point — nothing to run')
    check("refuses recursive delete",
          validate("x-y-z", good + 'import shutil\nshutil.rmtree("/")'), "contains recursive delete")
    check("refuses git writes",
          validate("x-y-z", good + '\nsubprocess.run(["git","push"])'), "contains git write")
    check("refuses network",
          validate("x-y-z", good + '\nurlopen("http://x")'), "contains network access")
    check("refuses file writes", validate("x-y-z", good + '\nopen("f","w")'), "contains file write")
    check("refuses oversized", validate("x-y-z", good + "#" * 9000)[:8], "too long")
    check("git write in shell form is refused",
          validate("x-y-z", good + '\nos.system("git push origin main")'), "contains git write")
    check("read-only git is allowed",
          validate("x-y-z", good + '\nsubprocess.run(["git","log","--oneline"])'), "")
    check("git status is allowed",
          validate("x-y-z", good + '\nsubprocess.run(["git","status","--short"])'), "")

    md = ('text\n```check my-check\nimport sys\nif __name__ == "__main__": sys.exit(0)\n```\n'
          '```python\nnot a check\n```\n')
    parsed = parse(md)
    check("parses only check blocks", len(parsed), 1)
    check("takes the slug from the fence", parsed[0][0], "my-check")

    # Executing a candidate must actually gate it.
    with tempfile.TemporaryDirectory() as tmp:
        p_ok = os.path.join(tmp, "ok.py"); open(p_ok, "w").write("import sys; sys.exit(0)")
        p_no = os.path.join(tmp, "no.py"); open(p_no, "w").write("import sys; sys.exit(1)")
        check("a passing check runs clean", run(p_ok, tmp)[0], True)
        check("a failing check is caught", run(p_no, tmp)[0], False)

    print(f"{15 - bad}/15 checks pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(run_tests() if "--test" in sys.argv else main(sys.argv[1:]))
