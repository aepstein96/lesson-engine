#!/usr/bin/env python3
"""Digest Claude Code session transcripts for lesson mining (/mine-transcripts).

Raw transcripts are JSONL up to ~13 MB per session — unreadable in-context. This strips
them to the dialogue that carries learning signals: user text in full (corrections,
interrupts, rephrasing), assistant text (backtracking, misreadings), one-line tool calls,
and truncated tool results (errors kept longer — they mark where sessions got stuck).
Thinking blocks and subagent sidechains are dropped.

A session started in a subfolder gets its own transcript dir, so this globs every
~/.claude/projects/ dir whose name starts with the flattened path of an added repo.

Usage:
  lesson-engine digest --out DIR          # digest sessions newer than marker
  lesson-engine digest --out DIR --all    # ignore the marker, digest all
  lesson-engine digest --count [--exclude SESSION_ID]   # how many are unmined
The marker is .claude/.transcripts-mined (gitignored); this script only READS it —
/mine-transcripts updates it after the mining agent is dispatched.

`--count` prints just an integer and backs the SessionStart hook (.claude/hooks/nudge-mine.sh),
so the "is there anything to mine?" test lives here once rather than being reimplemented in
bash — the marker and the transcript-dir globbing are fiddly enough that two copies would drift.
`--exclude` drops one session id (the hook passes the live session, whose transcript is being
written as we read it and is therefore always "newer than the marker").

Self-check:  lesson-engine digest --test
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

from . import config

USER_CHARS, TEXT_CHARS, TOOL_CHARS, RESULT_CHARS, ERROR_CHARS = 4000, 1500, 200, 200, 400

# --slim: same signals, fewer tokens. Measured composition of a full 12-session digest
# (1020 KB ≈ 261k tokens): tool calls 31%, tool RESULTS 29%, Claude prose 25%, human turns 13%,
# errors 1.5%. Human turns are where corrections live and are the cheapest 13% in the file, so
# slim never touches them; it cuts the bulky low-signal classes instead.
#
# An A/B against a full digest of the same two sessions found slim MORE productive per token
# (8 proposals for 120k tokens vs 7 for 200k), but its "evidence gaps" report named a real
# regression: dropping successful tool results entirely left the agent unable to tell whether
# any check had passed, so it could not size a dead end ("I cannot tell whether that was a
# 2-call or a 6-call detour"). Results are therefore kept but clipped hard rather than dropped —
# 80 chars is enough for pass/fail and an error's first line, which is what the judgement needs.
SLIM = False
SLIM_TOOL_CHARS, SLIM_TEXT_CHARS, SLIM_RESULT_CHARS = 100, 700, 80


# Transcripts contain whatever was typed into the chat, and humans paste secrets into chat no
# matter how clearly they were asked not to (a live Cloudflare API token was pasted twice in one
# session, after Claude had explicitly said "don't paste it into the chat"). A digest is then
# fanned out to fresh mining agents, turning one paste into N copies in N contexts. Deleting the
# file from disk afterwards, as that session did, protects the disk and nothing else.
# Redaction happens at digest time so a secret cannot reach a mining agent at all.
SECRET_RE = re.compile(
    r"""(?ix)
    \b(?: cfat_ | sk-[A-Za-z0-9]* | ghp_ | gho_ | ghs_ | github_pat_ | glpat- | xox[baprs]-
        | AKIA | ASIA | AIza | ya29\. | eyJ[A-Za-z0-9_-]*\. )
    [A-Za-z0-9_./+-]{12,}
    | \b(?:token|secret|password|passwd|api[_-]?key|auth)\b \s* [:=] \s* ["']? [A-Za-z0-9_./+-]{16,}
    | \bBearer \s+ [A-Za-z0-9_./+-]{16,}
    """)


def _redact(s: str) -> str:
    return SECRET_RE.sub("«redacted-secret»", s)


def _clip(s: str, n: int) -> str:
    s = _redact(" ".join(str(s).split()))
    return s if len(s) <= n else s[:n] + " …[clipped]"


def _clip_call(s: str, n: int) -> str:
    """Clip a tool call from the MIDDLE, keeping both ends within the same budget as _clip.

    Head-only truncation hides exactly the discriminating part of a tool call: every Read of a
    long absolute path is identical for its first 70 characters, and the repo's own scratchpad
    paths are 90+ characters before the filename. A mining agent given head-clipped lines could
    not tell one file read 8x (real waste) from 8 sibling files (normal work) and dropped three
    silent-waste candidates rather than guess — the class the feature exists to find. Same total
    tokens, strictly more information."""
    s = _redact(" ".join(str(s).split()))
    if len(s) <= n:
        return s
    head = int(n * 0.6)
    return f"{s[:head]} …[mid-clipped]… {s[-(n - head):]}"


def _result_text(content) -> str:
    """tool_result content is a string or a list of {'type':'text',...} items."""
    if isinstance(content, list):
        return " ".join(i.get("text", "") for i in content if isinstance(i, dict))
    return str(content or "")


def digest_line(o: dict) -> list[str]:
    """Render one transcript JSON object as zero or more digest lines."""
    if o.get("isSidechain") or o.get("type") not in ("user", "assistant"):
        return []
    content = (o.get("message") or {}).get("content")
    ts = (o.get("timestamp") or "")[:16]
    out = []
    if o["type"] == "user":
        if isinstance(content, str):
            out.append(f"[{ts}] USER: {_clip(content, USER_CHARS)}")
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_result":
                    text = _result_text(item.get("content"))
                    if item.get("is_error"):
                        out.append(f"  [tool ERROR] {_clip(text, ERROR_CHARS)}")
                    elif text.strip():
                        out.append(f"  [result] "
                                   f"{_clip(text, SLIM_RESULT_CHARS if SLIM else RESULT_CHARS)}")
                elif item.get("type") == "text":
                    out.append(f"[{ts}] USER: {_clip(item.get('text', ''), USER_CHARS)}")
    else:
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                out.append(f"[{ts}] CLAUDE: "
                           f"{_clip(item.get('text', ''), SLIM_TEXT_CHARS if SLIM else TEXT_CHARS)}")
            elif item.get("type") == "tool_use":
                out.append(f"  [tool {item.get('name')}] "
                           f"{_clip_call(json.dumps(item.get('input', {})), SLIM_TOOL_CHARS if SLIM else TOOL_CHARS)}")
    return out


# Silent waste — work the human never saw because Claude caught itself first — leaves no error
# and no correction, so there is no marker to grep. It has a SHAPE instead, and shape is
# computable: long unattended tool runs, the same call repeated, explicit backtracking. These
# are computed here and printed as pointers at the top of each digest, rather than left to the
# mining agent to eyeball across ~200 KB per session, which is the ambient-awareness form of
# the same request.
#
# RUN_MIN is measured, not guessed: across this repo's 21 sessions / 143 unattended runs the
# median run is 6 tool calls, p90 is 28, max 239. 20 sits near p85 and flags ~1.2 runs per
# session — dense enough to be worth reading, and deliberately permissive because a false
# positive costs the agent one glance while a false negative is the missed lesson we are here
# for. A pointer is "read this region first", never an accusation.
# Backtracking phrases are a deliberately WEAK third signal: Claude's visible text between tool
# calls is sparse (749 blocks across this repo's 21 sessions) and real backtracking happens in
# thinking, which the digest drops on purpose. Measured yield is ~0.1 hits/session, so the two
# structural signals above carry this feature; treat a phrase hit as a bonus, not as coverage.
BACKTRACK = ("let me back up", "that didn't work", "i was wrong", "never mind", "scratch that",
             "my mistake", "i misread", "on second thought", "let me reconsider", "turns out",
             "wait —", "wait,", "actually, ", "that was wrong", "let me rethink", "oops")
RUN_MIN, REPEAT_MIN = 20, 3

# Repeats only count tools where doing the same thing twice means work was REDONE: re-reading a
# file whose content wasn't retained, re-running an unchanged command, or hitting the same error.
# Edit/Write are excluded — editing one file many times is how work looks, not waste. (Keying on
# a 120-char prefix collapsed every Edit into one bucket and reported "repeated 26x" for 26
# distinct edits; the key is now the whole clipped line.)
REPEAT_TOOLS = ("  [tool Read]", "  [tool Bash]", "  [tool Grep]", "  [tool ERROR]")


def _is_human(line: str) -> bool:
    """A real human turn, not a tool result (which the digest also renders as a user entry)."""
    return line.startswith("[") and "] USER: " in line[:30]


def hotspots(lines: list[str]) -> list[str]:
    """Pointers to likely silent waste: (count, start, end) runs, repeats, backtracking."""
    runs, backs, calls = [], [], {}
    cur, cur_start, last_ts = 0, "", ""
    for ln in lines:
        ts = ln[1:17] if ln.startswith("[") else ""
        if _is_human(ln):
            if cur:
                runs.append((cur, cur_start, last_ts))
            cur, cur_start, last_ts = 0, "", ts or last_ts
        elif ln.startswith("[") and "] CLAUDE: " in ln[:30]:
            last_ts = ts or last_ts
            low = ln.lower()
            hit = next((p for p in BACKTRACK if p in low), "")
            if hit:
                backs.append((last_ts, hit))
        elif ln.startswith("  [tool "):
            if not ln.startswith("  [tool ERROR]"):
                if not cur:
                    cur_start = last_ts
                cur += 1
            if ln.startswith(REPEAT_TOOLS):
                calls[ln] = calls.get(ln, 0) + 1
    if cur:
        runs.append((cur, cur_start, last_ts))

    out = []
    for n, a, b in sorted(runs, reverse=True):
        if n >= RUN_MIN:
            out.append(f"  unattended run: {n} tool calls, no human turn, {a} → {b}")
    for call, n in sorted(calls.items(), key=lambda kv: -kv[1]):
        if n >= REPEAT_MIN:
            out.append(f"  repeated {n}x: {call.strip()}")
    for ts, phrase in backs:
        out.append(f"  backtracking at {ts}: \"{phrase}\"")
    return out


def count_lines(path: str) -> int:
    """Events in a transcript. JSONL is append-only, so this is a resumable watermark."""
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def digest_file(path: str, start_line: int = 0) -> str:
    """Digest a transcript, optionally only the events from start_line on.

    A live session's transcript keeps growing after it is read, so a whole-file digest re-reads
    everything already processed. `start_line` lets a caller digest just the tail (see
    survey_transcripts.py, which keeps a per-session watermark). cwd/branch are still
    scanned from the top so a partial digest carries the same header as a full one.
    """
    lines, cwd, branch = [], "", ""
    with open(path, errors="replace") as f:
        for n, raw in enumerate(f):
            try:
                o = json.loads(raw)
            except Exception:
                continue
            cwd, branch = o.get("cwd") or cwd, o.get("gitBranch") or branch
            if n >= start_line:
                lines.extend(digest_line(o))
    sid = os.path.basename(path).removesuffix(".jsonl")
    span = f"  lines {start_line}+" if start_line else ""
    head = [f"=== session {sid}  cwd={cwd}  branch={branch}{span} ==="]
    spots = hotspots(lines)
    if spots:
        head += ["--- SILENT-WASTE HOTSPOTS (computed; read these regions first) ---"] + spots
        head += ["--- end hotspots ---"]
    return "\n".join(head + lines) + "\n"


def target_transcripts() -> list[str]:
    """Every session transcript of every added repo. One level, because a session started in a
    subfolder gets its own transcript dir, and not recursive: nested agent-*.jsonl files are
    subagent sidechains, which hold no human turns."""
    paths: set[str] = set()
    for root in config.targets():
        flat = os.path.realpath(root).replace("/", "-")
        paths.update(glob.glob(os.path.expanduser(f"~/.claude/projects/{flat}*/*.jsonl")))
    return sorted(paths)


def unmined(all_: bool = False, exclude: str = "") -> list[str]:
    """Transcripts of the added repos that are newer than the mined marker."""
    marker = config.marker()
    since = 0.0 if all_ or not marker.exists() else marker.stat().st_mtime
    return [p for p in target_transcripts()
            if os.path.getmtime(p) > since
            and (not exclude or os.path.basename(p) != f"{exclude}.jsonl")]


def main() -> int:
    global SLIM
    args = sys.argv[1:]
    SLIM = "--slim" in args
    if not config.targets():
        print("no repos added yet: run `lesson-engine add <repo>`", file=sys.stderr)
        return 2

    if "--count" in args:
        ex = args[args.index("--exclude") + 1] if "--exclude" in args else ""
        print(len(unmined("--all" in args, ex)))
        return 0

    out_dir = args[args.index("--out") + 1] if "--out" in args else None
    if not out_dir:
        print("usage: lesson-engine digest --out DIR [--all] | --count [--exclude ID]",
              file=sys.stderr)
        return 2
    os.makedirs(out_dir, exist_ok=True)
    written = 0
    for p in unmined("--all" in args):
        dst = os.path.join(out_dir, os.path.basename(p).removesuffix(".jsonl") + ".txt")
        with open(dst, "w") as f:
            f.write(digest_file(p))
        print(f"{dst}  ({os.path.getsize(dst) // 1024} KB from {os.path.getsize(p) // 1024} KB)")
        written += 1
    print(f"{written} session digest(s) written to {out_dir}"
          + ("" if written else " — nothing newer than the marker; nothing to mine"))
    return 0


def run_tests() -> int:
    mk = lambda t, c, **kw: {"type": t, "timestamp": "2026-07-30T12:00:00Z",
                             "message": {"content": c}, **kw}
    cases = [
        (mk("user", "no, that's wrong — use the other file"), ["USER: no, that's wrong"]),
        (mk("assistant", [{"type": "text", "text": "Let me retry."},
                          {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                          {"type": "thinking", "thinking": "SECRET"}]),
         ["CLAUDE: Let me retry.", "[tool Bash]"]),
        (mk("user", [{"type": "tool_result", "is_error": True, "content": "boom"}]),
         ["[tool ERROR] boom"]),
        (mk("user", [{"type": "tool_result",
                      "content": [{"type": "text", "text": "FAIL: 6 modules"}]}]),
         ["[result] FAIL: 6 modules"]),
        (mk("user", "subagent chatter", isSidechain=True), []),
        ({"type": "file-history-snapshot"}, []),
    ]
    bad = 0
    for obj, wants in cases:
        got = digest_line(obj)
        joined = "\n".join(got)
        if len(got) != len(wants) or not all(w in joined for w in wants):
            bad += 1
            print(f"FAIL want {wants} got {got}")
    assert "SECRET" not in "\n".join("\n".join(digest_line(o)) for o, _ in cases)
    assert len(_clip("x" * 9000, 100)) < 120

    # _redact(): a secret must never reach a mining agent's context.
    secrets = ["cfat_RBb27G6Mexample0123456789abcdef", "sk-ant-api03-AAAABBBBCCCCDDDDEEEE",
               "ghp_0123456789abcdefghijklmnopqrstuvwxyz", "AKIAIOSFODNN7EXAMPLE1234",
               "token: abcdef0123456789abcdef", "Bearer abcdef0123456789abcdef",
               "glpat-ABCDEFGHIJKLMNOPQRST"]
    for sec in secrets:
        if sec.split()[-1][:12] in _redact(sec):
            bad += 1
            print(f"FAIL secret survived redaction: {sec[:12]}…")
    keep = "the report shows 1141.7 and a sha256 of the file; see docs/DEPLOY.md line 67"
    if _redact(keep) != keep:
        bad += 1
        print(f"FAIL redaction ate ordinary prose: {_redact(keep)}")
    redaction_cases = len(secrets) + 1

    # _ends(): both ends of a long tool call must survive, or repeats look like siblings.
    long_read = '{"file_path": "/home/x/repos/project/studies/abc/app/src/App.tsx"}'
    if "App.tsx" not in _clip_call(long_read, 60):
        bad += 1; print("FAIL _clip_call dropped the discriminating tail")
    if len(_clip_call("x" * 500, 100)) > 130:
        bad += 1; print("FAIL _clip_call exceeded its budget")
    if _clip_call("short", 60) != "short":
        bad += 1; print("FAIL _clip_call mangled a short string")
    redaction_cases += 3

    # hotspots(): the computed silent-waste pointers.
    human = lambda t: f"[2026-07-30T12:{t}] USER: do the thing"
    claude = lambda t, s: f"[2026-07-30T12:{t}] CLAUDE: {s}"
    tool = lambda name, arg: f'  [tool {name}] {{"x": "{arg}"}}'
    err = "  [tool ERROR] <tool_use_error>File has not been read yet.</tool_use_error>"
    long_run = [human("00")] + [tool("Bash", i) for i in range(RUN_MIN + 1)] + [human("59")]
    hs = [
        ("long unattended run is flagged",
         any("unattended run" in s for s in hotspots(long_run)), True),
        ("a short run is not",
         any("unattended" in s for s in hotspots([human("00"), tool("Bash", 1), human("01")])),
         False),
        ("tool results do NOT reset the run (only a real human turn does)",
         any("unattended" in s for s in hotspots(
             [human("00")] + [x for i in range(RUN_MIN + 1)
                              for x in (tool("Bash", i), "  [result] ok")])), True),
        ("repeated Read is flagged",
         any("repeated 3x" in s for s in hotspots([tool("Read", "a")] * 3)), True),
        ("repeated Edit is NOT flagged — many edits to one file is work, not waste",
         any("repeated" in s for s in hotspots([tool("Edit", "a")] * 5)), False),
        ("a repeated error is flagged",
         any("repeated 3x" in s for s in hotspots([err] * 3)), True),
        ("errors do not inflate the unattended-run count",
         any("unattended" in s for s in hotspots([human("00")] + [err] * (RUN_MIN + 5))), False),
        ("backtracking is picked up",
         any("backtracking" in s for s in hotspots([claude("05", "Wait, I was wrong.")])), True),
        ("ordinary prose is not backtracking",
         any("backtracking" in s for s in hotspots([claude("05", "Reading the config now.")])),
         False),
    ]
    for name, got, want in hs:
        if got != want:
            bad += 1
            print(f"FAIL {name}: want {want} got {got}")

    # unmined(): the SessionStart hook's "is there anything to mine?" test. Built against a
    # fake HOME so it exercises the real globbing, marker and exclude logic.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        home, root = os.path.join(tmp, "home"), os.path.join(tmp, "repo")
        os.makedirs(root)
        cfg = os.path.join(tmp, "cfg")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "targets.txt"), "w") as f:
            f.write(root + "\n")
        proj = os.path.join(home, ".claude", "projects",
                            os.path.realpath(root).replace("/", "-") + "-studies-x")
        os.makedirs(proj)
        real_home = os.environ.get("HOME", "")
        real_cfg = os.environ.get("LESSON_ENGINE_CONFIG_DIR")
        os.environ["HOME"] = home
        os.environ["LESSON_ENGINE_CONFIG_DIR"] = cfg
        try:
            for sid in ("old", "live"):
                open(os.path.join(proj, f"{sid}.jsonl"), "w").close()
            checks = [
                ("no marker → both unmined", len(unmined()), 2),
                ("exclude drops the live one", len(unmined(exclude="live")), 1),
                ("exclude is exact, not substring", len(unmined(exclude="liv")), 2),
            ]
            marker = str(config.marker())
            open(marker, "w").close()
            os.utime(marker, (2 ** 31, 2 ** 31))  # marker far in the future = all mined
            checks.append(("marker newer than all → nothing to mine", len(unmined()), 0))
            checks.append(("--all ignores the marker", len(unmined(all_=True)), 2))
        finally:
            os.environ["HOME"] = real_home
            if real_cfg is None:
                os.environ.pop("LESSON_ENGINE_CONFIG_DIR", None)
            else:
                os.environ["LESSON_ENGINE_CONFIG_DIR"] = real_cfg
    for name, got, want in checks:
        if got != want:
            bad += 1
            print(f"FAIL {name}: want {want} got {got}")
    total = len(cases) + len(checks) + len(hs) + redaction_cases
    print(f"{total - bad}/{total} cases pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(run_tests() if "--test" in sys.argv else main())
