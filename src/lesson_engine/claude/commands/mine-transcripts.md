---
description: Mine session transcripts for confusion and corrections, and propose lessons.
---

Mine past session transcripts for the learning that never reached any committed file: places
where Claude got confused or the human had to correct it. `/lesson` and `/harvest-lessons` cover the
*current* session; this covers the conversations themselves, across every repo added with
`lesson-engine add`.

First, run `lesson-engine paths`. It prints `LESSONS`, `MINED`, `INBOX` and `CAN_REVIEW`; everything
below uses those. Keep this run's working files in a durable folder, never `/tmp`, which a reboot
wipes (it took a run's digests and reports with it once): `RUN=~/.cache/lesson-engine/$(date -u +%F)`.

0. **Survey locally first — this is what stops the run exhausting a usage cap.** An open-weight
   model on this machine reads every digest and flags the human turns that show friction, so the
   Claude agents in step 2 read an index instead of megabytes:
   ```bash
   lesson-engine survey --out "$MINED/surveys"            # ~90s/session
   lesson-engine survey --out "$MINED/surveys" --status   # coverage
   ```
   Needs `ollama serve` running and `gpt-oss:20b` pulled; if ollama is down the script says so and
   exits rather than half-working. It is resumable per session by transcript line
   (`$MINED/surveys/INDEX.json`), so a live session's later turns get picked up on a rerun and
   a killed run keeps every session it finished.

   **A survey is an INDEX INTO the digests, not a replacement for them.** It gives you flagged
   turns with timestamps plus the computed hotspots — enough to decide where to look, never enough
   to write a lesson, because a correction line does not say what preceded it. Measured recall is
   66% against hand-scored ground truth with think=off; read `--status` and treat an
   `N UNLABELLED` header as a session to re-survey, not a quiet one.

1. **Digest the transcripts** (raw ones are up to ~13 MB; the script strips them to dialogue):
   ```bash
   lesson-engine digest --out "$RUN/digests"
   ```
   It digests only sessions newer than the mined marker (`--all` overrides). If it reports nothing
   to mine, say so and stop. Leave out the live session: its transcript is still being written.

2. **Dispatch a background `researcher` agent** on the SURVEYS, with the digests available for
   drill-down (researcher because it is read-only: transcripts embed downloaded web content, which
   must stay away from push access). Give each agent `$MINED/surveys/*.md` for its sessions and
   the matching digest paths, with this instruction: *read the survey first, then read only the
   regions of the digest around the flagged timestamps and hotspots.* A full backlog of digests is
   ~2.6 MB against ~30 KB of surveys, and reading all of it is what killed three agents on the
   2026-08-02 run. Split the session list across several agents; skip empty surveys.

   The surveys cover only what the local pass can see — human friction, plus the arithmetic
   hotspots. **Silent waste is still the agent's own hunt**, and the survey cannot flag it, because
   the class is defined by the human never having reacted.

   **Tell each agent to RETURN its report as its final message. Don't ask it to write a report file:
   the harness refuses those from subagents. As soon as each report arrives, save it yourself to
   `$RUN/report-<n>.md`, before doing anything else.**
   Parallel agents cannot see each other, so their proposals overlap and nothing reconciles them
   unless step 4 can read them all. (Measured on two agents over the same
   two sessions: 15 raw proposals → 11 distinct, only 4 found by both. Per-agent recall against
   the merged set was 64% and 73%; agreement was 36%. So a single pass misses roughly a third of
   what a second pass over identical input finds, and cross-agent duplication is guaranteed
   rather than incidental — two cheaper passes beat one expensive pass, but only if something
   merges them.)

   **Each proposal must end with a verdict line**, so the verdict is the agent's reproducible
   judgement rather than something the compiling session invents afterwards:
   ```
   VERDICT: check | lesson | merge:<entry> | contradicts:<entry>   CONFIDENCE: high | low
   ```
   **`contradicts` is the important one and the easiest to miss.** A proposal can say an existing
   entry is *wrong*, not merely duplicated — and a list of prose has no other way to be corrected,
   since nothing executes it. A false entry is worse than a missing one: it is read into every
   session and acted on (`CLAUDE.md` claimed for months that `git add studies/<slug>` from inside a
   study "exits 0 having staged nothing"; it exits 128, and sessions were told the failure was
   silent when it is loud). When the transcripts show an entry's factual claim failing to hold,
   say so under `contradicts` rather than proposing a politely-worded near-duplicate beside it.
   A `lesson` verdict must also carry the exact append-ready text inside a fenced block tagged
   `lesson`. That text is appended to the lesson list mechanically by
   `lesson-engine promote`; no human retypes it, and no agent writes that file directly.
   The block holds ONE bullet in the list's existing style: starts `- **`, continuation lines
   indented two spaces, no headings, no nested fences, no URLs, under 1200 characters. The
   promote script refuses anything else — read its docstring for why those rules are what keep
   untrusted transcript text out of a file every future session imports.
   `check` = a hook/assert/script step would prevent recurrence outright, so ship that instead of
   prose. `lesson` = transferable but not mechanisable. `merge` = an existing entry already covers
   the shape and should gain a clause. Default to `check` when a check is nameable — the list's
   escalation ladder prefers it, and a prose-only entry is the form that recurs.

   Its brief — **what counts as a mistake**:
   - **Human corrections** — the user contradicts or fixes something Claude said or did;
     rephrases an instruction already given (the first framing didn't land); interrupts an
     action mid-flight; declines a proposed action or permission request.
   - **Claude confusion** — retrying a failing command unchanged; misreading output (e.g.
     treating a harness failure as a finding); long dead ends abandoned after many tool
     errors; re-deriving by hand something a command/skill/doc already provides; guard-hook
     fires (`[tool ERROR]` and permission-ask lines mark these).
   - **Silent waste — the hardest and most valuable class.** Work the human never saw, because
     Claude caught itself first: a wrong approach pursued for many tool calls and then
     abandoned, a file read repeatedly because its content wasn't retained, a rabbit hole that
     ended in a quiet pivot. Nothing flags these — there is no error and no correction — so
     hunt them by **shape** rather than by marker:
     - Claude's own backtracking words in the `CLAUDE:` lines (*actually*, *wait*, *let me
       back up*, *that didn't work*, *I was wrong*, *never mind*) — each marks a self-caught
       error, and the lesson is whatever knowledge would have prevented the detour.
     - A long run of `[tool …]` lines between two `USER:` turns whose output does not show up
       in what the session finally did. Time spent, nothing kept.
     - The same file read, or the same question re-derived, more than twice in one session.
     These are worth more than the corrected mistakes, because a mistake the human had to fix
     is one they already know about; this class is invisible to everyone but the transcript.
     Note the digest drops thinking blocks, so a detour reasoned through silently leaves only
     its tool trace — judge by what was *done*, and say so when the evidence is only the shape.
   For each candidate: quote the evidence (session id + the lines), name the **root cause**
   (what knowledge, had it been in context, would have prevented it), then apply the
   the lesson list bar exactly as `/harvest-lessons` defines it — transfers to an unrelated
   domain, phrased domain-neutrally with the evidence as a parenthetical, deduped hard against
   the current list (quoting the entry checked). Prefer the check-shippable form: if a hook,
   assert, or script step would prevent recurrence outright, propose that instead of prose.
   **Score the existing lessons too, in both directions.** The list is only worth its reading cost
   if entries earn it, and until now mining only ever looked for failures — so a lesson that works
   was indistinguishable from one nobody reads. Report every entry the transcripts show:
   - **applied** — a session cited it, or visibly followed it, and was better off. This is real
     evidence and the cheapest to spot, because sessions tend to name the rule they are obeying
     (one measured a distribution rather than guessing a threshold, explicitly citing the
     free-parameters entry — a lesson written about datasets transferring to tooling design).
   - **violated** — the entry existed, was in context, and the session did the thing anyway.
     Repeated violations are an EFFICACY problem, not a truth problem: the claim is right and the
     prose is not holding, so the fix is promotion to a check, never a reworded restatement.
   - **contradicted** — the entry's factual claim is false. See the `contradicts` verdict above.
   Record each against the ledger so confidence tracks evidence rather than age:
   ```bash
   lesson-engine ledger --record <slug> --kind applied|violated --source <run>
   ```
   **Date the lesson before scoring it as a violation.** A session that predates an entry is
   where that entry CAME FROM, not evidence it is failing, and the two are indistinguishable
   from the transcript alone — scoring an origin as a violation would argue for promoting a
   rule that has never actually been tested. Get each entry's birthday with:
   ```bash
   git -C "$LESSONS_DIR" log --format='%ad %h' --date=short -S'<a distinctive phrase from the entry>' -- LESSONS.md CLAUDE.md | tail -1
   ```
   (both paths, in case the list's history includes an earlier home). A violation is a
   session **after** that date. Report origins separately — they are corroboration, and a
   lesson whose origin count is high but whose post-dating violation count is zero is working.
   **The agent proposes; it must not write the lesson list or commit.** That file governs every
   future session.

3. **Don't mark anything mined yet.** That happens in step 5, once the collated file exists. A run
   that dies before then should leave its sessions counted as unmined, not silently lost.

4. **Collate — do NOT do this by hand.** When more than one agent ran, dispatch ONE more
   `researcher` over `<digest-dir>/report-*.md` (it reads reports, not transcripts, so it is
   cheap — reports are a few KB against the digests' ~1 MB). Its whole job is the reconciliation
   no individual agent could do:
   - **Merge duplicates across reports**, keeping the best-evidenced version and recording that
     N agents found it independently — an independently-rediscovered proposal is *corroborated*,
     which is a signal none of the source reports can carry on its own.
   - **Group by the `VERDICT:` lines the agents already assigned**, and flag any it disagrees
     with rather than silently re-deciding — the point of the verdict living in the brief is that
     it survives into the future without a human re-inventing the taxonomy each run.
   - **Rank by evidence strength**: how many distinct sessions, and whether the mistake recurred
     after the relevant rule already existed.
   - Copy each surviving proposal's fenced `lesson` block through unchanged; the promote script
     reads the collated file, so a block dropped here cannot land. When two agents proposed the
     same lesson, keep the better-worded block.
   - **Resolve every `contradicts`, and make the call — this is the review's one real judgement.**
     Two agents can also contradict *each other* about the same incident (in the validation run one
     scored an entry as an origin, the other as a violation; dating it showed both were right about
     different things). Decide by evidence, in this order:
       1. **Executable beats asserted.** If one side ran the command, read the exit code, or
          measured the file and the other is reasoning, the measured side wins outright. No amount
          of confident prose outranks one observation.
       2. **Dated beats undated.** A session predating an entry is that entry's origin, not a
          refutation of it. Date before scoring (the `git log -S` recipe above).
       3. **If neither side is measured, do not decide.** Record it as unresolved, name the
          one-line experiment that would settle it, and leave the entry standing. A contradiction
          you cannot adjudicate is a reason to go measure, not a reason to rewrite the list.
     A resolved contradiction never silently deletes the old entry. Record it against the entry so
     its confidence reflects the hit, and propose the correction as its own `lesson` block:
     ```bash
     lesson-engine ledger --slug "<part of the entry's title>"      # find the key
     lesson-engine ledger --record <slug> --kind contradicted \
         --source <this run> --note "<what was measured, and where>"
     ```
     That flips it to `disputed`, which outranks any amount of accumulated support — one
     falsification beats any number of retellings.
   - Return the result as its final message. Save it yourself to **`$MINED/$(date -u +%F)-collated.md`**
     and report its path. It is plain markdown, meant to be read by a human. Keeping it in the lesson
     store is the point: mining runs in a background agent, and output left in a session's scratch
     space is lost when that session ends.

   Skipping this step is how a run ends as N overlapping documents that a human reconciles by
   hand — which is unreproducible, and means the next run starts from nothing. It is the step
   that turns parallel mining into one reviewable list.

5. **Mark the sessions mined:** `lesson-engine mark-mined`. If `CAN_REVIEW` is `no`, you don't own
   the list: move the collated file to `$INBOX/<your-username>-<date>-collated.md`, skip the ledger,
   promotion and pruning, and stop here. The list's owner reviews it.

   **Otherwise, promote the `lesson` verdicts — this step is autonomous by design.** The point of the
   pipeline is that the repo teaches itself; a loop that stops for a human every time is a person
   learning with extra steps. Preview, then apply:
   ```bash
   lesson-engine promote mined/<file>.md            # preview
   lesson-engine promote mined/<file>.md --apply    # append
   ```
   It appends at most 5 entries per run, skips any already in the list, and refuses malformed
   ones. Then, in the lesson store's repo, commit `LESSONS.md` and the collated file together. **The commit is the
   review** — applied after the fact instead of before, which is what keeps the loop autonomous
   while every change stays visible and revertable with one `git revert`.

   Two things stay gated, for reasons of kind rather than caution:
   - **`check` verdicts are code.** A hook or script step runs in every future session, and a bad
     one breaks work rather than merely misinforming it. Propose in the inbox; a human ships it.
   - **`merge` verdicts rewrite an existing entry.** That is a read-modify-write on a shared file,
     which the repo only sanctions inside `/harvest-lessons`.

   Then mark each proposal in the inbox file as landed or struck through with one line saying why
   not, so a later reader can tell "rejected" from "never got to it", and clean up the digest
   directory (digests can hold secrets, redacted only at digest time — anything built before
   2026-07-30 is unredacted).

   Commit the inbox file even when nothing is promoted. A run that found nothing worth keeping is
   a real result, and the record of having looked is what stops the next run re-mining the same
   ground.

6. **Prune in the same pass, or the list only grows.** the lesson list is read in full by every
   session, so its length is a budget, not a ratchet, and an autonomous adder with no remover
   converges on a list nobody reads:
   ```bash
   lesson-engine prune           # report candidates
   lesson-engine prune --apply   # remove near-identical duplicates
   ```
   It auto-removes only near-identical duplicates, because deleting is riskier than adding — a bad
   addition is noise, a bad deletion loses knowledge that was preventing real mistakes. Entries it
   flags as *superseded* (the lesson names a check that now exists on disk) are reported for
   judgement, never removed automatically: naming a file is not proof the check covers the whole
   lesson. Removed entries are archived to `$MINED/PRUNED.md` with the date and reason, so the
   record outlives the list.

**When to run it: whenever there is anything unmined.** The SessionStart hook
(`lesson-engine nudge`) reports the backlog at the start of every session, which is the
one moment an interruption is free — the session has invested nothing yet, and mining runs in a
background agent that never blocks the work. Check the count yourself any time with:

```bash
lesson-engine digest --count
```

This is the repo's **primary** lesson-capture channel, not a backstop. Rules written into
`CLAUDE.md` only catch mistakes a session notices it is making; mining catches the rest, and
it is the only mechanism that can see one mistake recurring across *different* sessions —
a live session sees only itself. It costs the working session nothing because the transcript
is written by the harness either way.
