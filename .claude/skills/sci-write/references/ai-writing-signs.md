# AI-slop signs — the de-slop checklist

Adapted from Wikipedia's "Signs of AI writing" (en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing,
read 2026-07-12) and repurposed: there the list detects AI text; here it is a **revision checklist**
— every draft gets a pass against this file before it is called done.

**The core caveat, from the page itself:** these surface markers are signs of the problem, not the
problem. The underlying disease is *content-free writing* — sentences that sound analytical but
assert nothing checkable. Deleting "delve" from a hollow paragraph leaves a hollow paragraph.
Fix the thought first (what exactly is claimed? what evidence? what number?), then the wording.
Also: humans produce many of these patterns too; the test is density. One em-dash is style;
five per page plus rule-of-three plus "underscores" is slop.

## 1. Content-level slop (worst offenders — these kill grant applications)

| Sign | Pattern | Examples to hunt down |
|---|---|---|
| Significance inflation | Generic claims of importance with no mechanism or number | "stands as a testament", "plays a vital/pivotal/crucial role", "underscores its importance", "marks a significant shift", "key turning point", "evolving landscape", "indelible mark", "deeply rooted", "setting the stage for" |
| Superficial -ing analysis | A present-participle clause bolted onto a fact, pretending to analyze it | "…, highlighting the importance of…", "…, underscoring the need for…", "…, ensuring robustness", "…, reflecting broader trends", "…, fostering collaboration", "…, enhancing our understanding" |
| Promotional puffery | Press-release adjectives in place of evidence | "groundbreaking", "cutting-edge", "state-of-the-art" (as decoration), "novel" (undefended), "vibrant", "rich", "renowned", "seamlessly", "boasts", "diverse array", "profound" |
| Vague attribution | Claims hung on nobody | "studies show", "experts argue", "it is widely recognized", "industry reports", "observers have cited", "some critics argue" — cite the actual paper or cut the claim |
| Editorializing | Opinion injected as fact | "importantly," "notably," "interestingly," "it is important to note that", "it is worth noting" |
| Formulaic challenges/outlook | "Despite its X, Y faces challenges… With ongoing efforts, Y continues to…" | Any "Challenges and Future Directions" paragraph that could be pasted into a different paper unchanged |
| Section summaries | Final sentence that restates the paragraph/section | "In summary", "In conclusion", "Overall, …" closing a section that was already clear |
| False range | Breadth theater | "from X to Y" constructions implying comprehensive coverage ("from ancient methods to modern AI") |

## 2. Sentence-level tics

- **AI vocabulary** (density test — any two of these in one paragraph is a red flag):
  delve, tapestry, testament, underscore, pivotal, crucial, intricate/intricacies, interplay,
  meticulous(ly), landscape (metaphorical), realm, garner, bolster, foster(ing), showcase/showcasing,
  highlight(ing) (as analysis), emphasize/emphasizing (as analysis), leverage (as verb),
  robust (undefended), holistic, multifaceted, paradigm, synergy, "align with", "resonate with",
  enduring, vibrant, enhance (vague), valuable insights.
- **Copula avoidance**: "serves as", "stands as", "represents", "features", "boasts", "holds the
  distinction of being" — where "is" would do. Write "is".
- **Negative parallelisms**: "not only X but also Y"; "It's not just X, it's Y"; "not X, but Y";
  "no X, no Y, just Z". One per document, maximum, and only if the contrast is the actual point.
- **Rule of three**: triads of adjectives, clauses, or examples used as rhythm rather than because
  exactly three things exist. If the third item adds nothing, cut it. Two precise items beat
  three padded ones.
- **Elegant variation**: cycling synonyms to avoid repeating a word ("constraints → obstacles →
  confines"). In technical writing, repetition of the exact term is correct; a synonym implies a
  different concept. Call the fault an "epistemic fault" every time; do not rotate to "shortcoming".
- **Connector chains**: paragraphs stitched with "Additionally, … Moreover, … Furthermore, …".
  If the logic is real, the sentences connect without traffic signs; if it isn't, the connector
  is hiding a non-sequitur.
- **Hedging stacks**: "could potentially", "may possibly", "it seems that … might". One hedge per
  claim, chosen deliberately (see writing-craft.md on calibrated claims).

## 3. Punctuation and formatting tics

- **Em-dash overuse** — the single most-recognized tell, and a user hard rule here: no em-dash
  unless essential (essential ≈ a genuine interruption or appositive that commas/parentheses/colon
  would mangle; at most a few per page, never several per paragraph). Prefer commas, colons,
  parentheses, or two sentences.
- **Bold-face sprinkling**: bolding every occurrence of key terms, listicle-style. In prose
  documents, bold almost nothing.
- **Bullet-list overuse / inline-header lists**: "**Header:** explanation" bullets in place of
  argued prose. Grant reviewers read arguments, not slide decks; convert to paragraphs unless the
  content is genuinely enumerable (criteria, materials, work packages).
- **Title Case Headings** where sentence case is the venue's norm (most journals, DFG, EU templates).
- **Curly quotes/apostrophes** pasted into LaTeX/plain-text submission systems; scare quotes around
  ordinary phrases.
- **Emoji, thematic-break rules before headings, skipped heading levels, uniform paragraph lengths.**
- Tables where prose belongs (and vice versa).

## 4. Citation red flags (fatal in applications — panels do check)

- Invented or wrong DOIs/ISBNs; DOIs resolving to unrelated papers.
- Dead links; book citations with no page numbers for specific claims.
- Citations that don't say what the sentence claims (the most common LLM citation failure —
  verify every citation against the actual source, not memory).
- Tool artifacts in text: "oaicite", "contentReference", "turn0search0", "utm_source=chatgpt.com",
  ":::" fences, markdown syntax in a non-markdown venue.
- References listed but never cited, or cited but never listed.

## 5. Chatbot remnants (instant desk-reject material)

"As an AI language model…", "As of my last knowledge update…", "I hope this helps",
"Certainly!", "Would you like me to…", placeholder text ("[insert institution]"),
refusal fragments, abrupt mid-sentence cutoffs, sycophancy toward the reader.

## The de-slop pass (run on every draft, in this order)

1. **Content pass**: for each paragraph ask "what is the checkable claim here, and where is its
   evidence/number/citation?" Paragraphs with no answer get rewritten or deleted — not reworded.
2. **Grep pass**: search the draft for the Section-2 vocabulary list, "not only", "-ing," clause
   commas, "serves as", "it is important", "In summary", "Additionally". Judge each hit in context.
3. **Punctuation pass**: count em-dashes per page; kill bold; check heading case and quote style.
4. **Citation pass**: open every reference; confirm it exists, the metadata matches, and it
   actually supports the sentence citing it.
5. **Read-aloud pass**: uniform sentence rhythm and interchangeable paragraphs read as machine
   cadence. Vary sentence length deliberately; make each paragraph do one job no other paragraph does.
