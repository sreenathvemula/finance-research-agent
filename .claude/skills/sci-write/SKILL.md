---
name: sci-write
description: High-quality scientific writing for research papers and grant/fellowship applications (MSCA-PF, DFG Eigene Stelle, Humboldt Fellowship, GFZ Discovery), tuned for seismology / natural hazards / AI-for-geoscience. Enforces the user's writing standard — precise flow of thought, deep technical engagement, no AI slop, no em-dash unless essential — plus a de-slop revision pass built from Wikipedia's "Signs of AI writing". Use whenever the user asks to write, draft, edit, review, tighten, or de-slop ANY scientific text: paper sections, abstracts, proposals, fellowship applications, motivation letters, response-to-reviewers, talk abstracts — even if they don't name this skill. Also use when they ask "does this read like AI?" or want text made more precise or more human.
---

# Sci-Write

Purpose: produce scientific text a tired expert reviewer trusts. Two failure modes kill drafts:
shallow content (sentences that sound analytical but assert nothing checkable) and AI-pattern
prose (the surface tics reviewers now recognize instantly). This skill guards against both, in
that order — content first, surface second, because de-slopping a hollow paragraph just yields
a hollow paragraph.

## The user's standard (non-negotiable, applies to every draft)

1. **Precise flow of thought.** Each sentence opens with what links back (old information) and
   ends on its new point; the next sentence picks that point up. This stress-to-topic chaining
   is what "flow" mechanically is — if two adjacent sentences could be swapped without loss, the
   flow is broken. Full mechanics: [references/writing-craft.md](references/writing-craft.md) §A.
2. **Deep technical engagement — never shallow.** Every paragraph must carry a checkable claim:
   a named method, dataset, quantity, or citation, and what exactly it shows. When reviewing
   literature, state what each work actually did and where it stops, not that it "highlights the
   importance of" something. If you don't know the specifics, say so and ask or research; do not
   pad with generalities.
3. **No AI slop.** Run every draft through the checklist in
   [references/ai-writing-signs.md](references/ai-writing-signs.md) before calling it done.
4. **No em-dash unless essential.** Essential means a genuine interruption or appositive that
   commas, parentheses, a colon, or two sentences would mangle. Default to those alternatives.
5. **Clarity above cleverness.** Plain verbs, subject next to verb, one point per unit, the same
   term for the same concept every time (a synonym implies a different concept). If a sentence
   needs rereading, rewrite it.

## Workflow

1. **Identify the document type and venue first.** Paper vs. proposal changes the architecture
   (below). For the four fellowship schemes, read
   [references/funding-schemes.md](references/funding-schemes.md) — each has hard page limits,
   mandated section orders, and named rejection causes; text is scored against the funder's
   template bullets, not against abstract quality.
2. **Outline before drafting**: one sentence per intended paragraph, in order. Check the outline
   carries the argument by itself. Fix structure here — vague feedback on a draft almost always
   means broken story structure, not bad wording.
3. **Draft** to the architecture below, writing from the reader's expectations
   ([references/writing-craft.md](references/writing-craft.md)).
4. **Revise in two separate passes**: first content (every paragraph's checkable claim), then
   the de-slop pass ([references/ai-writing-signs.md](references/ai-writing-signs.md), which
   ends with the full 5-step revision procedure).
5. **Verify every citation** against the actual source — existence, metadata, and that it
   supports the exact sentence citing it. Fabricated or mismatched citations are fatal in review,
   and they are the most common LLM citation failure.

## Research papers — the introduction contract

The user's required flow, which matches the Swales CARS model and Mensh & Kording Rule 6:

1. **Opening paragraph(s): why this problem matters.** Real stakes — deaths, cost, a physical
   unknown, an operational need — with a number and a citation in the first half-page. Do not
   open with the subfield's internal conversation or a truism every reader already holds.
2. **Literature review that converges.** Progressively narrowing paragraphs, each ending on what
   remains unknown. Critique and synthesize so the gap emerges as the review's inevitable
   conclusion; the reader should feel the objective coming before it is stated. Never bolt
   "however, no one has done exactly this" onto a neutral summary — that is the tell of a
   pasted-on gap. For mature fields, honor the field first, then show precisely where it stops
   (see the FDSN example in
   [references/example-proposals.md](references/example-proposals.md) §B).
3. **Occupy the gap.** State the question or hypothesis (questions beat vague objectives), the
   approach, and the principal findings.

Rest of the paper: abstract tells the complete story (context → gap → why it matters → method →
result → interpretation); results as declarative statements, figure titles stating conclusions;
discussion opens with what was shown, owns limitations by linking them to literature, and ends
with what the contribution unlocks — never "more research is needed". One central contribution
per paper, stated in the title.

## Proposals — a different arc, not a paper without results

Reviewers are impatient; front-load. The first page decides the score, because unassigned
panelists read only the summary and assigned reviewers form their view in minutes, then look for
confirmation. With no results to show, credibility comes from: preliminary work mapped onto the
objectives, method specificity (named data, instruments, algorithms, with fallbacks), a feasible
timeline, and honest risks with mitigations.

Standing structure (adapt to each scheme's template, which always wins):

1. **Why the problem matters** — same discipline as a paper opening: stakes anchor + quantified
   trend in the first half-page (three named disasters + a census table; a benchmark simulation
   with hard numbers — see the real openings in
   [references/example-proposals.md](references/example-proposals.md) §B).
2. **State of the art → gap** — a funnel ending in one explicit gap sentence, complete enough to
   be read without consulting the citations (DFG reviewers judge only the proposal text).
3. **Objectives** — 2–5, numbered, verbs like quantify/characterize/benchmark, each with expected
   outcome, related but independent so one failure cannot sink the project, each mapping 1:1 to
   a work package.
4. **Work programme** — per-objective methods, a question→data→method→outcome matrix where it
   fits, scope exclusions stated, Gantt chart, risks with mitigations (a no-risk plan reads as
   non-credible everywhere).
5. **Impact, in two separate registers** — advancement of science (what the field can do
   afterwards that it cannot do now) and societal path (who uses it, through which channel:
   early-warning centers, codes and standards, open datasets, policy). Each claim concrete enough
   for a reviewer to repeat in one sentence. No "will revolutionize".

Scheme specifics — page limits, scoring weights, section orders, eligibility traps (Helmholtz
duty-to-cooperate for DFG at GFZ; Humboldt's 90-day rule; GFZ's internal section nomination;
MSCA's two-way transfer narrative) — live in
[references/funding-schemes.md](references/funding-schemes.md). Study the real funded texts and
the rejected-with-reviewer-feedback ones in
[references/example-proposals.md](references/example-proposals.md) before drafting a new scheme.

## Register

Write like the human expert the user is: first person where the venue allows, varied sentence
length, concrete verbs, controlled hedging (one deliberate hedge per claim, not stacked
qualifiers), no bullet lists where an argument belongs in prose, bold almost nothing. Emphasis
comes from sentence position, not typography.

## Reference files

- [references/ai-writing-signs.md](references/ai-writing-signs.md) — the de-slop checklist +
  5-step revision pass. Read before finalizing ANY text.
- [references/writing-craft.md](references/writing-craft.md) — sentence/paragraph mechanics
  (Gopen & Swan), document architecture (Mensh & Kording, Schimel), proposal-vs-paper arcs,
  process rules. Read when drafting or when prose feels choppy.
- [references/funding-schemes.md](references/funding-schemes.md) — MSCA-PF, DFG Eigene Stelle,
  Humboldt, GFZ Discovery: structures, scoring, deadlines, pitfalls. Read before any
  application work.
- [references/example-proposals.md](references/example-proposals.md) — real proposal library
  with links + extracted patterns. Read before drafting a proposal section, to imitate structure
  (never wording).
