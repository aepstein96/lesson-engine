#!/usr/bin/env python3
"""Survey session digests with a LOCAL open-weight model, so /mine-transcripts reads KB not MB.

The backlog measured 2.6 MB of digest across 35 sessions (~660k tokens). Feeding that to Claude
mining agents is what exhausts a usage cap mid-run — twice already, per mined/2026-08-02.
This runs an open-weight model over the digests on this machine and emits a short survey per
session; the Claude agent then judges ~30 KB of pre-located incidents instead of hunting through
megabytes. Nothing leaves the machine, which matters because a digest is only pattern-redacted.

**Division of labour, deliberately.** Python does everything arithmetic can do: it extracts the
developer's real messages (dropping harness-generated pseudo-turns) and computes the repeat /
unattended-run / backtracking hotspots. The model answers exactly one question it cannot —
*does this message show the assistant had got something wrong?* — for every message, in order.
It does NOT author lessons, assign VERDICT: lines, or dedup against LESSONS.md; that is
judgement, it is cheap once the input is small, and a 20B asked for it produces confident mush.

**The model returns an index; Python attaches the text.** A fabricated quote is therefore
impossible by construction. What can still go wrong is a bad index or a message never answered
for, so both are counted and the survey header says `N UNLABELLED` when coverage is short —
a model that answers for 20 of 53 messages must not read like a clean session.

**Recall, honestly.** Calibrated on faf5e0ff, which holds 5 hand-scored content corrections:
each prompt lens finds 3, the two together find 4 (**80%**), and they miss different ones. An
earlier "66%" for this script counted bare "[Request interrupted by user]" lines, which are an
exact string match Python does for free — that inflated the score with work the model never did.
Interrupts are now context on the following message, not flags, which removed 37 of the first
backlog run's 65 outputs. Run-to-run variance is real: re-measure over several runs, not one.

**Resumable, per session, by transcript line.** A live session's transcript keeps growing after
it is surveyed, so a single mtime marker (what /mine-transcripts uses) either re-reads a 404 KB
session in full or misses everything appended after the first pass. `INDEX.json` beside the
surveys records, per session, how many transcript lines each pass covered — so the default run
surveys only the tail, and a session can be picked up as many times as it is added to. It also
records what produced each pass (model, thinking level, the script's own commit, UTC timestamp),
because a survey is an artifact of the tool that made it: a pass by qwen3:0.6b and a pass by
gpt-oss:20b are not interchangeable evidence, and neither are passes either side of a prompt edit.

Usage:
  ollama serve &  &&  ollama pull gpt-oss:20b
  lesson-engine survey --out mined/surveys       # new sessions + new tails
  lesson-engine survey --out DIR --all                # re-survey from line 0
  lesson-engine survey --out DIR --sessions faf5e0ff,df92085a   # calibration
  lesson-engine survey --out DIR --status             # what is covered, no model
  lesson-engine survey --model qwen3:30b-a3b --think off --out DIR

Self-check (no network, no model):  lesson-engine survey --test
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import subprocess
import sys

from . import config
import urllib.error
import urllib.request

MODEL = "gpt-oss:20b"
HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
THINK = "off"  # measured: think=low made gpt-oss reason correctly and then emit a TRUNCATED
# answer — its trace named messages 1,4,36,48 and it returned only [1]. medium/high found
# nothing at all, one taking 296s to do it. Omitting the field beats every thinking level here.
LEDGER = "INDEX.json"  # lives in the output dir, so a calibration run cannot pollute the real one

# 24k chars ≈ 6k tokens. Small models' recall decays long before their context window does, and
# the biggest single session here is 404 KB — one call was never on the table. Overlap keeps an
# incident that straddles a boundary visible in one whole piece to at least one call.
CHUNK_CHARS, OVERLAP_LINES = 24_000, 6

# No `why` field, deliberately. It cost 13s a session, changed recall by nothing (3/5 with and
# without, same misses), and produced labels like "User statement without request" — the model
# cannot say what the assistant got wrong because it never sees what the assistant did. Feeding it
# that context was tried and is worse: recall fell to 1/5 and the batch took 2.5x as long. The
# quoted turn is the evidence; a paraphrase of it is not worth a field.
SCHEMA = {
    "type": "object",
    "properties": {"messages": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"},
                       "friction": {"type": "boolean"}},
        "required": ["index", "friction"]}}},
    "required": ["messages"],
}

# Harness-generated turns wear the USER label but no human typed them, and they are the bulk of a
# quiet session's "human" lines (9 of df92085a's 11). Dropping them before the model sees anything
# is free and stops a slash-command echo being classified as a correction.
NOISE = ("<local-command-caveat>", "<local-command-stdout>", "<command-name>",
         "<command-message>", "<command-args>", "<task-notification>", "<system-reminder>",
         "<bash-input>", "<bash-stdout>", "<user-prompt-submit-hook>",
         "Base directory for this skill:")  # plugin payload injected as if the human typed it

# An interrupt on its own says nothing — it was 37 of the first backlog run's 65 flags, i.e. the
# majority of the output, and every one read "[Request interrupted by user]" with no indication of
# why. The information is in what the assistant was doing when it was stopped and what the
# developer said next, so an interrupt is no longer a turn: it is an ANNOTATION on the turn that
# follows it. Detecting one needs no model at all — it is an exact string.
# Deliberately the PREFIX, with no closing bracket: the harness emits at least two forms,
# "[Request interrupted by user]" and "[Request interrupted by user for tool use]", and matching
# the first exactly let the second through as a contentless flag in a 35-session run.
INTERRUPT = "[Request interrupted by user"

TURN_CHARS = 600   # per turn in the numbered list; corrections announce themselves early

# Measured on faf5e0ff, whose 49 messages contain 5 hand-scored content corrections. Changing any
# of the following should mean re-running that calibration.
#
# 1. EXHAUSTIVE LABELLING, NOT SEARCH. Asked to "find the incidents" in a chunk of raw log,
#    gpt-oss:20b returned 1 of 7 and often 0 — every judgement gate is a chance for a small model
#    to talk itself out of reporting. Asked to emit one verdict for EVERY message there is no gate,
#    and it labels 49/49. The model is also NOT asked to spot repeats, unattended runs or
#    backtracking: mine_transcripts.py already computes those exactly, for free.
# 2. BINARY, WITH NO NEUTRAL ESCAPE. A seven-label version filed "i think pages was the wrong
#    choice", "actually use gaussian weighted", "bug: typing 23 inserts 1" and "did it redeploy?
#    doesnt seem so" all as `question` — the catch-all absorbed the whole class being hunted.
# 3. AN INDEX, NEVER A QUOTE. Python attaches the verbatim turn, so a fabricated quote is
#    impossible by construction rather than caught afterwards.
# 4. TWO LENSES, UNIONED. See PROMPT_REDIRECT: 3/5 each, 4/5 together, different misses.
#
# Run-to-run variance is real — the same config scored 2/5 and 3/5 on consecutive runs — so treat
# a single number as noisy and re-measure over several runs before believing a prompt change.
# The persistent miss is a redirect stated as a plain instruction ("hmm i think pages was the wrong
# choice") with no complaint and no reference to what it replaces.
PROMPT = """You are reading every message a developer sent during one session with an AI coding assistant.

Each numbered line is one message. A line may be marked to say the developer interrupted the assistant mid-action just before sending it.

For EVERY numbered message, output one entry, in order, with no gaps.

Set friction=true when the message shows the assistant had got something wrong:
- it reports a bug or defect in what was just built
- it contradicts, corrects or redirects ("actually use X instead", "no, the other one")
- it doubts or disputes that something worked ("did it deploy? doesnt seem so", "it's still broken")
- it says an earlier choice was wrong ("i think X was the wrong choice")
- it asks again for something already asked
- the developer INTERRUPTED the assistant and the message sends it somewhere else

Set friction=false only when the message asks for genuinely new work, asks for information the assistant had not already given, or approves ("yes", "do it"). An interrupt followed by a message that simply continues the same work is not friction.

A politely phrased complaint is still friction. A question that implies something failed is friction, not a question."""

# The second lens. Both prompts run over the same messages and their flags are UNIONed: each alone
# recovers 3 of 5 hand-scored corrections on faf5e0ff, together 4 of 5, and they miss DIFFERENT
# ones — the complaint lens misses a silent method swap, the undo/redo lens misses a doubted
# deploy. This is the same effect /mine-transcripts already documents for parallel Claude miners
# (per-agent recall 64-73%, agreement 36%), reproduced with one model and two framings for ~90s.
PROMPT_REDIRECT = """Below are a developer's messages to an AI coding assistant, numbered.

For EVERY message output one entry, in order, with no gaps.

Set friction=true if the message CHANGES DIRECTION on work the assistant had already done:
- it replaces a method or approach already chosen ("actually use X", "use Y instead")
- it revises something already built ("that graph should replace both", "make it blue instead")
- it says a prior decision was wrong ("I think X was the wrong choice", "this was a mistake")
- it reports the result was defective
- it interrupted the assistant to send it somewhere else

Set friction=false for a first-time request, a question, or approval.

The test is simple: would the assistant have to UNDO or REDO something it had already done? If yes, friction=true."""


def _load_digester():
    """Reusing digest_file()/unmined() rather than reforking them: the transcript-dir globbing, the
    marker semantics and the secret redaction are all fiddly, and a second copy would drift."""
    from . import mine_transcripts
    return mine_transcripts


def load_ledger(out_dir: str) -> dict:
    path = os.path.join(out_dir, LEDGER)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_ledger(out_dir: str, led: dict) -> None:
    # ponytail: no locking. Two concurrent surveys of the same dir would race; run one.
    with open(os.path.join(out_dir, LEDGER), "w") as f:
        json.dump(led, f, indent=1, sort_keys=True)
        f.write("\n")


def watermark(rec: dict | None) -> int:
    """Transcript lines already surveyed. Derived from the passes, never stored twice."""
    return max((p.get("lines_to", 0) for p in (rec or {}).get("passes", [])), default=0)


def tool_version(root: str = "") -> str:
    """The installed engine version, so a survey can be traced to the code that made it."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        return "v" + version("lesson-engine")
    except PackageNotFoundError:
        return "uninstalled"


def chunks(text: str) -> list[str]:
    """Split a digest into overlapping line-aligned windows of at most CHUNK_CHARS."""
    lines, out, cur, size = text.splitlines(), [], [], 0
    for ln in lines:
        if cur and size + len(ln) > CHUNK_CHARS:
            out.append("\n".join(cur))
            cur, size = cur[-OVERLAP_LINES:], sum(len(x) + 1 for x in cur[-OVERLAP_LINES:])
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def _norm(s: str) -> str:
    return " ".join(s.split())


def human_turns(digest: str, is_human) -> list[dict]:
    """The developer's real messages, each with the context needed to judge it.

    `prev` is what the assistant had just said or run — without it the model can only paraphrase
    the message back ("User statement without request"), because it cannot see what was corrected.
    `interrupted` is the assistant's last action before a "[Request interrupted by user]", carried
    forward onto the next message so the interrupt is context rather than a contentless flag.
    """
    turns, prev_text, prev_tool, pending = [], "", "", None
    for ln in digest.splitlines():
        if is_human(ln):
            body = _norm(ln.split("] USER: ", 1)[-1])
            if any(n in body[:300] for n in NOISE):
                continue
            if INTERRUPT in body:
                # The TOOL first here, unlike `prev` below: an interrupt stops an action, and
                # "[tool Bash] npm run build" says what was stopped where "on it" does not.
                pending = prev_tool or prev_text
                continue
            turns.append({"text": _norm(ln), "prev": prev_text or prev_tool,
                          "interrupted": pending})
            pending, prev_text, prev_tool = None, "", ""
        elif "] CLAUDE: " in ln[:30]:
            prev_text = _norm(ln.split("] CLAUDE: ", 1)[-1])
        elif ln.startswith("  [tool "):
            prev_tool = _norm(ln)
    if pending is not None:  # interrupted with nothing after it — the session simply stopped
        turns.append({"text": "(the developer interrupted, and the session ended)",
                      "prev": pending, "interrupted": pending})
    return turns


def batch_line(i: int, t: dict) -> str:
    """One numbered entry as the model sees it.

    Deliberately does NOT include `prev`, the assistant's preceding action. Adding it was the
    obvious fix for the useless `why` labels and it measurably backfired: recall fell from 3/5 to
    1/5 and the batch took 2.5x as long (21k chars against 10.6k). `prev` is still carried on the
    turn — the markdown uses it for interrupts, where it is the whole point — just not fed here.
    """
    mark = "(the developer INTERRUPTED the assistant mid-action here) " \
        if t["interrupted"] is not None else ""
    return f"[{i}] {mark}{t['text'][:TURN_CHARS]}"


def _valid(i, turns) -> bool:
    return isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(turns)


def verify(found: list[dict], turns: list[str]) -> tuple[list[dict], int, int]:
    """Keep friction=true entries, attaching each turn's verbatim text by index.

    Returns (kept, bad_index_count, labelled_count). `labelled` is the coverage check: a model
    that answers for 20 of 53 messages has silently dropped 33, which looks identical to a clean
    session unless the count is carried out of here.
    """
    kept, seen, bad, labelled = [], set(), 0, 0
    for f in found:
        i = f.get("index")
        if not _valid(i, turns) or i in seen:
            bad += 1 if f.get("friction") else 0
            continue
        seen.add(i)
        labelled += 1
        if f.get("friction"):
            kept.append({**f, "evidence": turns[i]["text"],
                         "prev": turns[i]["prev"], "interrupted": turns[i]["interrupted"]})
    return kept, bad, labelled


def ask(chunk: str, model: str, think: str, prompt: str = PROMPT) -> list[dict]:
    body = {"model": model, "stream": False, "format": SCHEMA,
            "options": {"temperature": 0, "num_ctx": 16384},
            "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": chunk}]}
    if think != "off":
        body["think"] = think
    req = urllib.request.Request(f"{HOST}/api/chat", method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            content = json.loads(r.read())["message"]["content"]
    except urllib.error.HTTPError as e:
        # An arguments-shaped error is a lookup, not a retry: print what the server said.
        raise SystemExit(f"ollama {e.code}: {e.read().decode()[:400]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"cannot reach ollama at {HOST} ({e.reason}) — run `ollama serve`")
    try:
        return json.loads(content).get("messages", []) or []
    except json.JSONDecodeError:
        return []


def survey(digest: str, model: str, think: str, span: str, is_human) -> tuple[str, int, int]:
    """Survey one digest (or one tail of one). Returns (markdown pass block, kept, dropped)."""
    hot = [ln for ln in digest.splitlines()[:60] if ln.startswith("  ") and (
        "unattended run" in ln or ln.startswith("  repeated") or "backtracking at" in ln)]
    turns = human_turns(digest, is_human)
    numbered = [batch_line(i, t) for i, t in enumerate(turns)]

    by_index, dropped, labelled = {}, 0, 0
    parts = chunks("\n".join(numbered)) if numbered else []
    for lens, prompt in (("complaint", PROMPT), ("undo/redo", PROMPT_REDIRECT)):
        for i, ch in enumerate(parts, 1):
            print(f"    {lens} batch {i}/{len(parts)} ({len(ch) // 1000}k chars)", flush=True)
            kept, bad, seen = verify(ask(ch, model, think, prompt), turns)
            dropped += bad
            labelled = max(labelled, seen) if lens == "complaint" else labelled
            for k in kept:  # union across lenses; remember which ones agreed
                by_index.setdefault(k["index"], {**k, "lenses": []})["lenses"].append(lens)
    found = [by_index[i] for i in sorted(by_index)]

    gap = "" if labelled >= len(turns) else f" — **{len(turns) - labelled} UNLABELLED**"
    md = [f"## pass {span} — `{model}` think={think}",
          f"{len(turns)} human turns · {labelled} labelled{gap} · {len(found)} flagged"
          + (f", {dropped} bad index" if dropped else ""), ""]
    if hot:
        md += ["**computed hotspots** (arithmetic, not the model)", *[f"-{h}" for h in hot], ""]
    if found:
        md.append("### friction")
        for r in found:
            tag = "both lenses" if len(r["lenses"]) > 1 else f"{r['lenses'][0]} only"
            md.append(f"- `{r['evidence'][:400]}`  \n  *{tag}*")
            if r["interrupted"]:
                md.append(f"  · interrupted the assistant, which was running: "
                          f"`{r['interrupted'][:200]}`")
            elif r["interrupted"] is not None:
                md.append("  · interrupted the assistant (no preceding action in the digest)")
        md.append("")
    return "\n".join(md) + "\n", len(found), dropped


def main() -> int:
    args = sys.argv[1:]
    get = lambda f, d: args[args.index(f) + 1] if f in args else d
    out_dir, model, think = get("--out", None), get("--model", MODEL), get("--think", THINK)
    if not out_dir:
        print(__doc__.split("Usage:")[1], file=sys.stderr)
        return 2
    root = str(config.lessons_dir())
    out_dir = out_dir if os.path.isabs(out_dir) else os.path.join(root, out_dir)
    os.makedirs(out_dir, exist_ok=True)

    mt = _load_digester()
    # Every transcript this repo has, not the mtime marker: the ledger is a better watermark
    # because it is per session and knows how far into each one we got.
    # One level, matching /mine-transcripts: `{flat}*/` covers every dir a session started in
    # (a session started in a subfolder gets its own). Deliberately NOT recursive — the nested agent-*.jsonl
    # files are subagent sidechains, 201 of them here against 35 real sessions, and they hold no
    # human turns, which is where corrections live.
    paths = mt.target_transcripts()
    only = [s.strip() for s in get("--sessions", "").split(",") if s.strip()]
    if only:
        paths = [p for p in paths if any(os.path.basename(p).startswith(s) for s in only)]
        for s in only:
            if not any(os.path.basename(p).startswith(s) for p in paths):
                print(f"no transcript for: {s}", file=sys.stderr)

    led, redo = load_ledger(out_dir), "--all" in args
    todo = []  # (path, sid, start_line, total_lines)
    for p in paths:
        sid = os.path.basename(p).removesuffix(".jsonl")
        total, done = mt.count_lines(p), watermark(led.get(sid))
        if redo:
            todo.append((p, sid, 0, total))
        elif total > done:
            todo.append((p, sid, done, total))

    if "--status" in args:
        stale = [s for s, r in led.items()
                 if any(q.get("tool") != tool_version(root) for q in r.get("passes", []))]
        print(f"{len(paths)} transcripts · {len(led)} surveyed · {len(todo)} with new material")
        for p, sid, start, total in todo:
            print(f"  {sid[:8]}  lines {start}/{total}"
                  f"{'  (new)' if not start else '  (+%d)' % (total - start)}")
        if stale:
            print(f"{len(stale)} session(s) surveyed by an older tool version "
                  f"(current {tool_version(root)}): {', '.join(s[:8] for s in sorted(stale))}")
        return 0

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    tool = tool_version(root)
    tot_kept = tot_dropped = 0
    for p, sid, start, total in todo:
        digest = mt.digest_file(p, start)
        span = f"lines {start}–{total}"
        print(f"  {sid[:8]}  {span}  {len(digest) // 1024} KB", flush=True)
        md, kept, dropped = survey(digest, model, think, span, mt._is_human)
        dst = os.path.join(out_dir, f"{sid}.md")
        fresh = not os.path.exists(dst) or redo
        with open(dst, "w" if fresh else "a") as f:
            if fresh:
                f.write(f"# survey {sid}\n\n")
            f.write(md)
        rec = led.setdefault(sid, {"transcript": p, "passes": []})
        rec["transcript"] = p
        if redo:
            rec["passes"] = []
        rec["passes"].append({"date": stamp, "tool": tool, "model": model, "think": think,
                              "lines_from": start, "lines_to": total,
                              "bytes": os.path.getsize(p),
                              "incidents": kept, "dropped": dropped})
        save_ledger(out_dir, led)  # after each session: a killed run keeps what it finished
        tot_kept += kept
        tot_dropped += dropped

    rate = tot_dropped / (tot_kept + tot_dropped) * 100 if tot_kept + tot_dropped else 0
    print(f"{len(todo)} of {len(paths)} transcript(s) surveyed → {out_dir}\n"
          f"{tot_kept} incidents kept, {tot_dropped} dropped ({rate:.0f}% unverifiable quotes)")
    return 0


def run_tests() -> int:
    bad = 0
    # chunks(): windows are bounded, overlap, and lose no line.
    text = "\n".join(f"line {i} " + "x" * 200 for i in range(400))
    cs = chunks(text)
    checks = [
        ("splits a long digest", len(cs) > 1, True),
        ("every chunk within budget", all(len(c) <= CHUNK_CHARS + 250 for c in cs), True),
        ("no line is lost", all(f"line {i} " in "".join(cs) for i in (0, 199, 399)), True),
        ("chunks overlap", cs[0].splitlines()[-1] in cs[1], True),
        ("a short digest is one chunk", len(chunks("a\nb\nc")), 1),
        ("an empty digest yields nothing", chunks(""), []),
    ]
    # verify(): the model returns an index and Python supplies the text, so a fabricated quote is
    # impossible. What CAN go wrong is a bad index, and a silently-dropped one loses a real hit.
    T = lambda s, prev="", intr=None: {"text": s, "prev": prev, "interrupted": intr}
    turns = [T("[t] USER: build the thing"), T("[t] USER: no, that's wrong — use wrangler"),
             T("[t] USER: and make it blue")]
    F = lambda i, fr=True: {"index": i, "friction": fr}
    cases = [
        ("a flagged message is kept", [F(1)], 1, 0),
        ("an unflagged message is not", [F(0, False)], 0, 0),
        ("an out-of-range index is dropped", [F(7)], 0, 1),
        ("a negative index is dropped", [F(-1)], 0, 1),
        ("a non-integer index is dropped", [F("two")], 0, 1),
        ("a boolean is not an index", [F(True)], 0, 1),
        ("the same index twice counts once", [F(1), F(1)], 1, 1),
        ("a mixed batch keeps only the valid flagged ones",
         [F(0), F(99), F(2, False), F(1)], 2, 1),
    ]
    for name, found, want_k, want_d in cases:
        k, d, _ = verify(found, turns)
        checks.append((name, (len(k), d), (want_k, want_d)))
    checks += [
        ("the attached text is the turn itself, not the model's",
         verify([F(1)], turns)[0][0]["evidence"], turns[1]["text"]),
        # Coverage: a model answering for 20 of 53 messages must not look like a clean session.
        ("labelled counts every answered message, flagged or not",
         verify([F(0, False), F(1), F(2, False)], turns)[2], 3),
        ("labelled does not count messages the model skipped", verify([F(1)], turns)[2], 1),
    ]

    # human_turns(): harness-generated pseudo-turns are the bulk of a quiet session's USER lines.
    mt0 = _load_digester()
    digest = "\n".join([
        "[2026-08-02T11:00] USER: make the chart green",
        "[2026-08-02T11:01] CLAUDE: on it",
        "  [tool Edit] {}",
        "[2026-08-02T11:02] USER: <local-command-caveat>Caveat: the messages below…",
        "[2026-08-02T11:03] USER: <command-name>/effort</command-name> <command-args>xhigh",
        "[2026-08-02T11:04] USER: <task-notification> <task-id>abc</task-id>",
        "[2026-08-02T11:05] USER: [Request interrupted by user]",
        "[2026-08-02T11:06] USER: no — I said green, not teal",
    ])
    # The harness emits more than one interrupt wording; matching only the bare form let
    # "[Request interrupted by user for tool use]" through as a flag across a whole backlog run.
    variant = human_turns("\n".join([
        "[2026-08-02T11:00] USER: ship it",
        "  [tool Bash] {\"command\": \"deploy\"}",
        "[2026-08-02T11:01] USER: [Request interrupted by user for tool use]",
        "[2026-08-02T11:02] USER: not to prod",
    ]), mt0._is_human)
    checks += [
        ("the 'for tool use' interrupt variant is not a turn either", len(variant), 2),
        ("and it still annotates the next message",
         "deploy" in (variant[-1]["interrupted"] or ""), True),
    ]
    got = human_turns(digest, mt0._is_human)
    txt = [t["text"] for t in got]
    checks += [
        ("harness pseudo-turns are dropped", len(got), 2),
        ("a real turn survives", any("make the chart green" in t for t in txt), True),
        ("a slash-command echo is not a turn", any("/effort" in t for t in txt), False),
        ("a task notification is not a turn", any("task-notification" in t for t in txt), False),
        ("an injected skill payload is not a turn", any("ponytail" in t for t in txt), False),
        ("assistant lines never appear", any("CLAUDE" in t for t in txt), False),
        # An interrupt is context on the NEXT message, not a contentless flag of its own — it was
        # 37 of the first backlog run's 65 flags, all reading "[Request interrupted by user]".
        ("a bare interrupt is not its own turn",
         any(INTERRUPT in t for t in txt), False),
        ("the message after an interrupt is marked as interrupted",
         got[-1]["interrupted"] is not None, True),
        ("that mark carries what the assistant was doing when stopped",
         "tool Edit" in (got[-1]["interrupted"] or ""), True),
        ("a message with no interrupt before it is not marked",
         got[0]["interrupted"], None),
        ("each turn carries what the assistant had just said",
         "on it" in got[-1]["prev"] or "tool Edit" in got[-1]["prev"], True),
    ]

    # A trailing interrupt must not vanish: the session ending mid-action is itself the signal.
    tail = human_turns("\n".join([
        "[2026-08-02T11:00] USER: build it",
        "  [tool Bash] {\"command\": \"npm run build\"}",
        "[2026-08-02T11:01] USER: [Request interrupted by user]"]), mt0._is_human)
    checks += [
        ("a trailing interrupt still produces an entry", len(tail), 2),
        ("it says the session ended there", "session ended" in tail[-1]["text"], True),
        ("it keeps the interrupted action", "npm run build" in (tail[-1]["interrupted"] or ""),
         True),
    ]

    # batch_line(): what the model actually reads.
    bl = batch_line(3, T("[t] USER: no, blue", prev="  [tool Edit] {}", intr="  [tool Bash] {}"))
    checks += [
        ("the batch line is numbered", bl.startswith("[3] "), True),
        ("it announces an interrupt", "INTERRUPTED" in bl, True),
        # Measured to HURT: recall 3/5 -> 1/5 and 2.5x slower when prev was fed to the model.
        ("it does not carry the assistant's preceding action", "tool Edit" in bl, False),
        ("it ends with what the developer said", bl.endswith("no, blue"), True),
        ("no interrupt marker when there was none",
         "INTERRUPTED" in batch_line(0, T("[t] USER: hi")), False),
    ]
    # watermark(): the resume point. An off-by-one here silently skips a session's tail.
    checks += [
        ("an unknown session starts at 0", watermark(None), 0),
        ("a session with no passes starts at 0", watermark({"passes": []}), 0),
        ("one pass resumes at its end", watermark({"passes": [{"lines_to": 120}]}), 120),
        ("two passes resume at the furthest",
         watermark({"passes": [{"lines_to": 120}, {"lines_to": 340}]}), 340),
        ("out-of-order passes still resume at the furthest",
         watermark({"passes": [{"lines_to": 340}, {"lines_to": 120}]}), 340),
    ]

    # digest_file(start_line=): the tail must be exactly what a full digest lacks.
    import tempfile
    mt = _load_digester()
    ev = lambda i: json.dumps({"type": "user", "timestamp": "2026-08-10T09:00:00Z",
                               "cwd": "/repo", "message": {"content": f"turn number {i} here"}})
    with tempfile.TemporaryDirectory() as tmp:
        f1 = os.path.join(tmp, "s.jsonl")
        with open(f1, "w") as f:
            f.write("\n".join(ev(i) for i in range(10)) + "\n")
        full, tail = mt.digest_file(f1), mt.digest_file(f1, 7)
        checks += [
            ("count_lines counts events", mt.count_lines(f1), 10),
            ("a full digest has every turn", full.count("turn number"), 10),
            ("a tail digest has only the new ones", tail.count("turn number"), 3),
            ("the tail starts at the right turn", "turn number 7" in tail, True),
            ("the tail excludes what was already surveyed", "turn number 6" in tail, False),
            ("a tail digest keeps its header", "cwd=/repo" in tail, True),
            ("start_line past the end yields no turns", "turn number" in mt.digest_file(f1, 99),
             False),
        ]

    for name, got, want in checks:
        if got != want:
            bad += 1
            print(f"FAIL {name}: want {want} got {got}")
    print(f"{len(checks) - bad}/{len(checks)} cases pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(run_tests() if "--test" in sys.argv else main())
