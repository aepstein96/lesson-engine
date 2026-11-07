#!/usr/bin/env python3
"""Confidence ledger for LESSONS.md — evidence for and against each lesson.

A prose lesson has no way to be wrong. Nothing executes it, so nothing falsifies it, and a false
claim can sit in a file every session reads for months while sessions act on it (CLAUDE.md said
`git add studies/<slug>` from inside a study "exits 0 having staged nothing" — it exits 128; that
was only caught when a behavioural test happened to be built next to it). This ledger is the
substitute for execution: it records what later evidence did to each claim.

WHY A SEPARATE FILE. LESSONS.md is read in full by every session, so it stays clean prose —
IDs, counters and event logs inline would tax every future read for the benefit of tooling. The
ledger keys on a slug of each entry's bold title and never stores the lesson text, so it is
metadata about the list, not a second copy of it (the repo's "a rule duplicated into N files will
drift" rule is about copies of the RULE). Drift is made visible instead of prevented: --status
reports ledger entries whose lesson has vanished, and lessons with no ledger entry.

STATUS IS DERIVED, NEVER SET BY HAND. Adapted from an evidence-ledger pattern where status falls
out of the evidence rather than being asserted:

    disputed     ANY contradiction exists — takes precedence over any amount of support, because
                 one measurement that falsifies a claim outweighs any number of retellings
    established  3+ supporting events from 2+ distinct sources
    supported    2+ supporting events
    proposed     everything else (a claim recorded once and never re-observed)

Supporting events are `corroborated` (independently proposed again by a later mining run) and
`applied` (a session cited the lesson and it worked). Against: `contradicted` (evidence the claim
is false). `violated` is neither — the claim held, a session broke it — so it is tracked separately
as an EFFICACY signal: repeated violations mean the prose is not holding and the lesson should be
promoted to a check, which is a different problem from being wrong.

Usage:
  lesson-engine ledger --init                       # add missing lessons, keep events
  lesson-engine ledger                              # status report
  lesson-engine ledger --record <slug> --kind corroborated --source <run> [--note ...]
  lesson-engine ledger --slug "Measure, don't reason"   # resolve a title to its slug

Self-check:  lesson-engine ledger --test
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys

from . import config

KINDS = ("corroborated", "applied", "contradicted", "violated")
SUPPORTING = ("corroborated", "applied")


def slug(title: str) -> str:
    """Stable key from a lesson's bold title. Punctuation-insensitive so light rewording of
    the trailing clause does not orphan the entry."""
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return "-".join(s.split("-")[:8])


def lesson_titles(text: str) -> list[str]:
    """The bold title of every entry in LESSONS.md."""
    return [m.group(1).strip() for m in re.finditer(r"^- \*\*(.+?)\*\*", text, re.M | re.S)]


def derive(events: list[dict]) -> str:
    kinds = [e.get("kind") for e in events]
    if "contradicted" in kinds:
        return "disputed"
    support = [e for e in events if e.get("kind") in SUPPORTING]
    sources = {e.get("source") for e in support}
    if len(support) >= 3 and len(sources) >= 2:
        return "established"
    if len(support) >= 2:
        return "supported"
    return "proposed"


def load(path: str) -> dict:
    return json.load(open(path)) if os.path.exists(path) else {}


def save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def main(argv: list[str]) -> int:
    lessons_path = str(config.lessons_file())
    ledger_path = str(config.mined_dir() / "LEDGER.json")
    titles = lesson_titles(open(lessons_path).read())
    by_slug = {slug(t): t for t in titles}
    ledger = load(ledger_path)

    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    if "--slug" in argv:
        q = opt("--slug", "")
        hits = [(s, t) for s, t in by_slug.items() if q.lower()[:40] in t.lower()]
        for s, t in hits:
            print(f"{s}\n    {t[:80]}")
        if not hits:
            print(f"no lesson matches {q!r}", file=sys.stderr)
            return 1
        return 0

    if "--from" in argv:
        # Self-feeding: a mining run's collated file carries its own ledger events, so
        # confidence tracks evidence without anyone transcribing it by hand. Blocks look like:
        #     ```ledger
        #     slug: fix-the-shared-helper-not-the-call-site
        #     kind: violated
        #     note: screenshot pipeline forked inline 6x
        #     ```
        src = opt("--from")
        src = src if os.path.isabs(src) else os.path.join(root, src)
        if not os.path.exists(src):
            print(f"no such file: {src}", file=sys.stderr)
            return 2
        blocks = re.findall(r"^```ledger[ \t]*\n(.*?)^```[ \t]*$",
                            open(src).read(), re.M | re.S)
        source = os.path.basename(src).removesuffix(".md")
        applied = skipped = 0
        for b in blocks:
            f = dict(re.findall(r"^(\w+):\s*(.+)$", b, re.M))
            key, kind = f.get("slug", "").strip(), f.get("kind", "").strip()
            if kind not in KINDS or key not in by_slug:
                print(f"  SKIP  slug={key!r} kind={kind!r}"
                      f" ({'unknown kind' if kind not in KINDS else 'no such lesson'})")
                skipped += 1
                continue
            entry = ledger.setdefault(key, {"events": []})
            ev = {"date": _today(), "kind": kind, "source": source,
                  "note": f.get("note", "").strip()}
            if ev in entry["events"]:          # idempotent: re-applying a file changes nothing
                skipped += 1
                continue
            entry["events"].append(ev)
            entry["status"] = derive(entry["events"])
            applied += 1
            print(f"  {kind:<13} {key[:52]:<52} -> {entry['status']}")
        save(ledger_path, ledger)
        print(f"{len(blocks)} block(s): {applied} applied, {skipped} skipped")
        disputed = [s for s, e in ledger.items() if derive(e.get("events", [])) == "disputed"]
        if disputed:
            print(f"\n{len(disputed)} lesson(s) now DISPUTED — run scripts/prune-lessons.py")
        return 0

    if "--record" in argv:
        key, kind = opt("--record"), opt("--kind")
        if kind not in KINDS:
            print(f"--kind must be one of {KINDS}", file=sys.stderr)
            return 2
        if key not in by_slug:
            print(f"no lesson with slug {key!r}; try --slug '<part of its title>'", file=sys.stderr)
            return 2
        entry = ledger.setdefault(key, {"events": []})
        entry["events"].append({"date": _today(), "kind": kind,
                                "source": opt("--source", "unknown"),
                                "note": opt("--note", "")})
        before = entry.get("status", "proposed")
        entry["status"] = derive(entry["events"])
        save(ledger_path, ledger)
        moved = f"  {before} -> {entry['status']}" if before != entry["status"] else ""
        print(f"recorded {kind} on {key}{moved}")
        if entry["status"] == "disputed":
            print("  DISPUTED: a contradiction outranks any amount of support. Resolve it — "
                  "prefer whichever side has executable evidence — then correct or remove the "
                  "lesson. Do not simply add more support.")
        return 0

    if "--init" in argv:
        added = 0
        for s in by_slug:
            if s not in ledger:
                ledger[s] = {"status": "proposed", "events": []}
                added += 1
        save(ledger_path, ledger)
        print(f"{len(by_slug)} lessons; added {added} new ledger entr"
              f"{'y' if added == 1 else 'ies'} to {LEDGER_REL}")
        return 0

    # default: status report
    if not ledger:
        print(f"no ledger yet — run --init")
        return 0
    buckets: dict[str, list[str]] = {}
    for s, t in by_slug.items():
        st = derive(ledger.get(s, {}).get("events", []))
        buckets.setdefault(st, []).append(t)
    for st in ("disputed", "established", "supported", "proposed"):
        items = buckets.get(st, [])
        print(f"\n{st.upper()} ({len(items)})")
        for t in items:
            ev = ledger.get(slug(t), {}).get("events", [])
            marks = "".join("+" if e["kind"] in SUPPORTING else
                            "!" if e["kind"] == "contradicted" else "x" for e in ev)
            print(f"  {marks:<6} {t[:74]}")
    viol = [(t, sum(1 for e in ledger.get(slug(t), {}).get("events", [])
                    if e["kind"] == "violated")) for t in titles]
    repeat = [(t, n) for t, n in viol if n >= 2]
    if repeat:
        print("\nNOT HOLDING (repeat violations — promote to a check, this is not about truth)")
        for t, n in repeat:
            print(f"  {n}x  {t[:74]}")
    orphans = [s for s in ledger if s not in by_slug]
    missing = [t for s, t in by_slug.items() if s not in ledger]
    if orphans:
        print(f"\nORPHANED ledger entries (lesson gone or retitled): {', '.join(orphans)}")
    if missing:
        print(f"\n{len(missing)} lesson(s) not in the ledger — run --init")
    print("\nlegend: + supporting evidence   ! contradiction   x violation")
    return 0


def run_tests() -> int:
    bad = 0

    def check(name, got, want):
        nonlocal bad
        if got != want:
            bad += 1
            print(f"FAIL {name}: want {want!r} got {got!r}")

    check("slug is stable across punctuation",
          slug("Measure, don't reason — about the data!"), slug("Measure don't reason about the data"))
    check("slug is bounded", len(slug(" ".join(["word"] * 40)).split("-")) <= 8, True)
    check("distinct titles get distinct slugs",
          slug("Fix the shared helper") == slug("Print heuristics against a case"), False)

    md = ("prose\n- **First lesson here.** body\n  more\n- **Second, with punctuation!** body\n")
    check("finds every title", lesson_titles(md), ["First lesson here.", "Second, with punctuation!"])
    check("ignores prose", "prose" in " ".join(lesson_titles(md)), False)

    e = lambda k, src="r1": {"date": "2026-07-30", "kind": k, "source": src, "note": ""}
    check("no evidence is proposed", derive([]), "proposed")
    check("one support is proposed", derive([e("corroborated")]), "proposed")
    check("two supports is supported", derive([e("corroborated"), e("applied")]), "supported")
    check("three from one source stays supported",
          derive([e("corroborated"), e("applied"), e("applied")]), "supported")
    check("three from two sources is established",
          derive([e("corroborated"), e("applied", "r2"), e("applied", "r3")]), "established")
    check("a contradiction outranks any support",
          derive([e("corroborated"), e("applied", "r2"), e("applied", "r3"), e("contradicted")]),
          "disputed")
    check("violation is not evidence against truth", derive([e("violated"), e("corroborated")]),
          "proposed")

    print(f"{12 - bad}/12 checks pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(run_tests() if "--test" in sys.argv else main(sys.argv[1:]))
