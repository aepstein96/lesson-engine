#!/usr/bin/env python3
"""Find (and optionally remove) lessons that have stopped earning their place in LESSONS.md.

Promotion is autonomous; without a matching pruner the list only grows, and a list every session
reads in full converges on a length nobody reads. Its length is a budget, not a ratchet. Nothing
ever generated deletion candidates before, so `/harvest-lessons`' "prune in the same pass" was an
instruction with no input — this produces the input.

Two candidate classes, deliberately asymmetric because deleting is riskier than adding. A bad
addition is noise; a bad deletion loses knowledge that was preventing real mistakes.

  SUPERSEDED  the entry names an enforcement artifact that now exists on disk (a hook, a script
              step). This is the safest deletion in the repo's own words — "the check outlives
              the prose" — but "mentions a file" is not proof the check covers the whole lesson,
              so these are REPORTED, never auto-removed.
  DUPLICATE   two entries whose content words overlap above AUTO_SIM. Near-identical prose helps
              nobody and the survivor keeps the same knowledge, so this class alone may be
              auto-removed with --apply. The LONGER entry survives, on the assumption it carries
              more evidence.

Whatever is removed is appended to mined/PRUNED.md with the date and the reason — the
record outlives the list, so a pruned lesson can always be read back without git archaeology.

Usage:
  lesson-engine prune                      # report candidates, change nothing
  lesson-engine prune --apply              # remove the DUPLICATE class only
  lesson-engine prune --apply --entry 12   # also remove entry 12 by number
  lesson-engine prune --budget 40          # warn when the list exceeds N entries

Self-check:  lesson-engine prune --test
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys

from . import config

AUTO_SIM = 0.70     # auto-removable: near-identical
FLAG_SIM = 0.50     # worth a human look
BUDGET = 45         # entries; past this the list is long enough to stop being read in full
STOP = set("the a an and or but if to of in on for with is are was were be been it its this that "
           "as at by from not no than then so you your they their them we our can cannot will "
           "would should could may might do does did done have has had one two three when what "
           "which who whom whose there here every each any all some more most less least only "
           "into onto over under out up down about after before during while because since".split())


def entries_with_spans(text: str) -> list[tuple[int, int, str]]:
    """Every list entry as (start_line, end_line_exclusive, text). Entries are '- **' bullets
    plus their two-space continuation lines; prose sections around them are left alone."""
    lines = text.split("\n")
    out, start = [], None
    for i, line in enumerate(lines):
        if line.startswith("- **"):
            if start is not None:
                out.append((start, i, "\n".join(lines[start:i]).rstrip("\n")))
            start = i
        elif start is not None and line.strip() and not line.startswith("  "):
            out.append((start, i, "\n".join(lines[start:i]).rstrip("\n")))
            start = None
    if start is not None:
        out.append((start, len(lines), "\n".join(lines[start:]).rstrip("\n")))
    return out


def _words(entry: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", entry.lower()) if w not in STOP}


def similarity(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa | wb else 0.0


def find_duplicates(entries: list[str], threshold: float) -> list[tuple[int, int, float]]:
    """(loser_index, survivor_index, score) — the shorter entry loses."""
    pairs = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            s = similarity(entries[i], entries[j])
            if s >= threshold:
                loser, keeper = (i, j) if len(entries[i]) < len(entries[j]) else (j, i)
                pairs.append((loser, keeper, s))
    return pairs


def find_superseded(entries: list[str], root: str) -> list[tuple[int, str]]:
    """Entries naming a hook or script that exists — the check may already carry the lesson."""
    hits = []
    for i, e in enumerate(entries):
        for name in re.findall(r"`([\w./-]+\.(?:py|sh))`", e):
            base = os.path.basename(name)
            for d in (".claude/hooks", "scripts"):
                # An enforcing script lives in a repo the lessons apply to, so look in each of them.
                if any(os.path.exists(os.path.join(r, d, base)) for r in [root, *config.targets()]):
                    hits.append((i, f"{d}/{base} exists"))
                    break
            else:
                continue
            break
    return hits


def title_slug(entry: str) -> str:
    """Same key lesson-ledger.py uses, so the two agree on what a lesson is called."""
    title = entry.split("**")[1] if "**" in entry else entry
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return "-".join(s.split("-")[:8])


def disputed_slugs(root: str) -> dict[str, list[str]]:
    """slug -> contradiction notes, for lessons the ledger marks disputed."""
    path = str(config.mined_dir() / "LEDGER.json")
    if not os.path.exists(path):
        return {}
    out = {}
    for slug, entry in json.load(open(path)).items():
        notes = [e.get("note", "") for e in entry.get("events", [])
                 if e.get("kind") == "contradicted"]
        if notes:
            out[slug] = notes
    return out


def archive(root: str, removed: list[tuple[str, str]]) -> str:
    """Append removed entries to mined/PRUNED.md so the record outlives the list."""
    path = str(config.mined_dir() / "PRUNED.md")
    new = not os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    with open(path, "a") as f:
        if new:
            f.write("# Pruned lessons\n\nEntries removed from `LESSONS.md`, with the reason "
                    "and the date. The list is a budget, so entries leave it; the knowledge is "
                    "kept here rather than left to git archaeology.\n")
        for entry, why in removed:
            f.write(f"\n## {today} — {why}\n\n{entry}\n")
    return path


def main(argv: list[str]) -> int:
    root = str(config.lessons_dir())
    apply_ = "--apply" in argv
    budget = int(argv[argv.index("--budget") + 1]) if "--budget" in argv else BUDGET
    by_number = {int(argv[i + 1]) for i, a in enumerate(argv) if a == "--entry"}

    lessons = str(config.lessons_file())
    text = open(lessons).read()
    spans = entries_with_spans(text)
    items = [t for _, _, t in spans]
    print(f"{len(items)} entries in LESSONS.md (budget {budget})"
          + ("" if len(items) <= budget else f"  — OVER by {len(items) - budget}"))

    dupes = find_duplicates(items, FLAG_SIM)
    sup = find_superseded(items, root)
    doomed: dict[int, str] = {}

    # DISPUTED: the ledger holds evidence the claim is FALSE, not merely unhelpful. A false
    # entry is worse than a missing one — it is read into every session and acted on — so this
    # class is auto-removable like duplicates. It is archived with its contradiction note, so
    # "removed" means "moved to PRUNED.md with the reason", never "lost".
    disputed = disputed_slugs(root)
    if disputed:
        print("\nDISPUTED (evidence the claim is false — auto-removable):")
        for i, t in enumerate(items):
            key = title_slug(t)
            if key in disputed:
                print(f"  #{i + 1}  {t.split('**')[1][:60] if '**' in t else t[:60]}")
                for note in disputed[key]:
                    print(f"        contradicted: {note[:80]}")
                doomed[i] = f"disputed — {disputed[key][0][:70] or 'contradicted by later evidence'}"
    else:
        print("\nDISPUTED: none")

    print("\nDUPLICATE candidates:" if dupes else "\nDUPLICATE candidates: none")
    for loser, keeper, s in dupes:
        auto = s >= AUTO_SIM
        print(f"  #{loser + 1} vs #{keeper + 1}  similarity {s:.2f}"
              f"  {'AUTO-REMOVABLE' if auto else 'flag only'}")
        print(f"     drop: {items[loser].split('**')[1][:66]}")
        print(f"     keep: {items[keeper].split('**')[1][:66]}")
        if auto:
            doomed[loser] = f"duplicate of #{keeper + 1} (similarity {s:.2f})"

    print("\nSUPERSEDED candidates (reported only — judgement required):" if sup
          else "\nSUPERSEDED candidates: none")
    for i, why in sup:
        print(f"  #{i + 1}  {why}\n     {items[i].split('**')[1][:66]}")

    for n in by_number:
        if 1 <= n <= len(items):
            doomed[n - 1] = "removed by explicit --entry"
        else:
            print(f"\n--entry {n} is out of range 1..{len(items)}", file=sys.stderr)
            return 2

    if not apply_:
        print(f"\nreport only — {len(doomed)} entr{'y' if len(doomed) == 1 else 'ies'} would be "
              f"removed with --apply. Nothing changed.")
        return 0
    if not doomed:
        print("\nnothing to remove.")
        return 0

    # Removal is a read-modify-write, which the repo sanctions only in a deliberate pass. Keep the
    # window short: read, filter, write, in one go.
    drop_lines = set()
    for idx in doomed:
        s, e, _ = spans[idx]
        drop_lines.update(range(s, e))
    lines = text.split("\n")
    open(lessons, "w").write("\n".join(l for i, l in enumerate(lines) if i not in drop_lines))
    path = archive(root, [(items[i], why) for i, why in sorted(doomed.items())])
    print(f"\nremoved {len(doomed)} entr{'y' if len(doomed) == 1 else 'ies'}; "
          f"archived to {os.path.relpath(path, root)}")
    print("commit both files together — the diff is the review.")
    return 0


def run_tests() -> int:
    bad = 0

    def check(name, got, want):
        nonlocal bad
        if got != want:
            bad += 1
            print(f"FAIL {name}: want {want!r} got {got!r}")

    doc = ("# Header\n\nSome prose that must survive.\n"
           "- **First lesson.** Body text here.\n  continued here.\n"
           "- **Second lesson.** Different body.\n"
           "\n## A section\n\nmore prose\n"
           "- **Third lesson.** Another body.\n")
    spans = entries_with_spans(doc)
    check("finds every entry", len(spans), 3)
    check("captures continuation lines", "continued here." in spans[0][2], True)
    check("stops at a following heading", "## A section" in spans[1][2], False)
    check("prose is not an entry", any("Some prose" in t for _, _, t in spans), False)

    a = "- **A key that collapses records inflates counts.** Truncated keys merge distinct rows."
    b = "- **A key that collapses records inflates counts.** Truncated keys merge distinct rows!"
    c = "- **Measure a live source at a pinned revision.** Concurrent writers break cross-checks."
    check("near-identical scores high", similarity(a, b) > 0.9, True)
    check("unrelated scores low", similarity(a, c) < 0.3, True)
    check("similarity is symmetric", similarity(a, c) == similarity(c, a), True)

    pairs = find_duplicates([a, b, c], AUTO_SIM)
    check("duplicate pair found", len(pairs), 1)
    check("unrelated entry is not paired", len(pairs), 1)

    # Tie-break: of a near-identical pair the LONGER survives (it carries more evidence).
    # Note the extra words also lower similarity, so the pair must stay above AUTO_SIM to
    # be auto-removable at all — which is the intended behaviour, not a bug: two entries
    # that differ substantially in content are not duplicates.
    longer = a + " Extra."
    pairs2 = find_duplicates([longer, a], AUTO_SIM)
    check("near-identical pair still detected", len(pairs2), 1)
    check("shorter entry is the loser", pairs2[0][0], 1)
    check("longer entry is the keeper", pairs2[0][1], 0)

    diverged = a + " Extra evidence making this one materially longer and quite different indeed."
    check("substantive extra content drops it below auto",
          len(find_duplicates([diverged, a], AUTO_SIM)), 0)

    # Removal must not disturb the surrounding prose or the entries it keeps.
    lines = doc.split("\n")
    s, e, _ = spans[1]
    kept = "\n".join(l for i, l in enumerate(lines) if not (s <= i < e))
    check("removal drops the target", "Second lesson" in kept, False)
    check("removal keeps its neighbours", "First lesson" in kept and "Third lesson" in kept, True)
    check("removal keeps prose", "Some prose that must survive." in kept, True)
    check("removal keeps headings", "## A section" in kept, True)

    # title_slug must agree with lesson-ledger.py's slug(), or disputed entries never match.
    from . import lesson_ledger as ll
    for t in ("Measure, don't reason — about the data or the toolchain.",
              "Fix the shared helper, not the call site.",
              "A zero is not a \"no data\", and an empty join is not an answer."):
        check(f"slug agrees with ledger: {t[:28]}", title_slug(f"- **{t}** body"), ll.slug(t))

    print(f"{20 - bad}/20 checks pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(run_tests() if "--test" in sys.argv else main(sys.argv[1:]))
