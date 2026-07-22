#!/usr/bin/env python3
"""
22_transcript_parser.py â€” Parse concall transcript markdown into structured exchanges.

pymupdf4llm renders each speaker as a markdown header (## **Name**), so we split
on those to get clean speaker turns, then:

  1. Classify each speaker as operator / analyst / management.
     - operator : name contains "moderator" or "operator"
     - analyst  : introduced by the operator ("...line of X from Y", "X from Y")
     - management: everyone else (recurs in prepared remarks)
  2. Split the call into PREPARED REMARKS (before first operator turn) and Q&A.
  3. Group Q&A into EXCHANGES: one analyst question turn + the management answer
     turn(s) that follow, until the next analyst/operator turn.

Each exchange is one self-contained RAG chunk. A multi-part question stays WITH
its answer (never split) so context is preserved. Follow-ups from the same analyst
later in the call become separate exchanges, linked by analyst name + sequence.

Input : data/markdown/{SYMBOL}/concalls/*transcript*.md
Output: data/parsed/{SYMBOL}/concalls/{name}.json

Usage:
  python 22_transcript_parser.py --symbol INFY
  python 22_transcript_parser.py --all
  python 22_transcript_parser.py --symbol INFY --show   # print parsed summary
"""
import argparse, json, logging, re
from pathlib import Path

ROOT   = Path(__file__).parent.parent
MD_DIR = ROOT / "data" / "companies"
OUT    = ROOT / "data" / "parsed"
OUT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("tparse")

# Speaker labels come in two markdown styles depending on the source PDF:
#   colon style  : "**Raveen Kanabar:** Thank you..."  (name + colon, text may be inline)
#                  also "## **Name:**", "**Name**:", "Name:"
#   header style : "## **Ankur Rudra**"                 (name only, text on next lines)
_NAME = r"([A-Z][A-Za-z.'\-]+(?:\s+[A-Za-z.'\-]+){0,4}?)"
_ROLE = r"(?:\s*\([^)]*\))?"   # optional parenthetical role, e.g. "(HOST- EMKAY GLOBAL)"
COLON_RE  = re.compile(r"^\s*#{0,6}\s*\*{0,2}\s*" + _NAME + _ROLE + r"\s*\*{0,2}\s*:\s*\*{0,2}\s*(.*)$")
HEADER_RE = re.compile(r"^\s*(?:#{1,6}\s*\*{0,2}|\*{2})\s*" + _NAME + _ROLE + r"\s*\*{0,2}\s*$")
OPERATOR_WORDS = ("moderator", "operator")

# timestamp style : "**Sh V Srikanth** 00:00:35 - 00:08:40 **(Consolidated Financials)**"
# (RIL media/analyst calls; also appears as a bulleted agenda, whose empty turns drop out)
TS_RE = re.compile(r"^\s*[•·]?\s*\*\*\s*" + _NAME + _ROLE +
                   r"\s*\*\*\s*\d{1,2}:\d{2}(?::\d{2})?\s*[–—-]\s*\d{1,2}:\d{2}(?::\d{2})?")

# dash-role style : whole line bold, "name <dash> role/firm" (SBIN, BHARTIARTL):
#   "**Mr. Manish Ostwal – Fund Manager, Nirmal Bang Securities Pvt Ltd**"
#   "**Gopal Vittal – Managing Director & Chief Executive Officer - Bharti Airtel Limited**"
#   "**- Mr. Dinesh Khara – Chairman, State Bank of India**"
_HON = r"(?:Mrs|Mr|Ms|Dr|Shri|Smt|Sh|Prof)"
DASH_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?\*\*\s*[-–]?\s*"
    r"(" + _HON + r"\.?\s+)?"                                  # 1: honorific
    r"([A-Z][A-Za-z.']*(?:\s+[A-Z][A-Za-z.']*){0,3})"          # 2: title-case name
    r"\s*(?:[–—]|-)\s+"                                        # dash separator
    r"([^*\n]{2,110}?)"                                        # 3: role / firm
    r"\s*\*\*\s*$")
# words that make the dash-label's role part credibly a role/firm (not a section header)
_ROLEWORD = re.compile(r"(?i)\b(moderator|operator|director|officer|manager|chairman|chief|"
                       r"president|head|analyst|ceo|cfo|coo|cto|md|secretary|capital|securities|"
                       r"bank|research|fund|invest\w*|partner|advisors?|equit\w*|financ\w*|"
                       r"insurance|asset|mutual|broking|amc|host)\b|,|&")
# a bare proper-noun firm ("Motilal Oswal", "BNP Paribas") — >=2 capitalized words, no digits
_FIRM_NAME = re.compile(r"[A-Z][A-Za-z.&']*(?:\s+[A-Z&][A-Za-z.&']*){1,3}$")

# plain style : "Mahrukh Adajania:" / "Moderator:" alone on a line, no markup (ICICI).
# Only used as a fallback pass when the markup-based parse captured almost nothing.
PLAIN_RE = re.compile(r"^([A-Z][A-Za-z.'\-]*(?:\s+[A-Z][A-Za-z.'\-]*){0,3})\s*:\s*$")

# Recurring PDF page-header boilerplate rendered into the markdown (BHARTIARTL):
# "**Final Transcript**" + republication notice repeat on every page and split
# speaker turns mid-sentence ("Final Transcript" even parses as a speaker name).
# Treat them as page breaks: drop the lines, joining the surrounding text.
_PAGE_HEADER = re.compile(
    r"^\s*(?:#{1,6}\s*)?\*{0,2}\s*Final Transcript\s*\*{0,2}\s*$"
    r"|^\s*\*{0,2}\s*Republished with permission\.? No part of this publication", re.I)
_PAGE_TITLE = re.compile(r"(?i)transcript|conference call|earnings call")


def strip_page_headers(md: str) -> str:
    """Remove per-page header/footer lines so pages join back into one flow."""
    from collections import Counter
    lines = md.splitlines()
    counts = Counter(l.strip() for l in lines if l.strip())
    out = []
    for l in lines:
        s = l.strip()
        if _PAGE_HEADER.match(s):
            continue
        # an identical bold/heading title line recurring 3+ times is a page header
        if (counts[s] >= 3 and len(s) > 20 and _PAGE_TITLE.search(s)
                and re.match(r"^(?:#{1,6}\s*)?\*\*.+\*\*$", s)):
            continue
        out.append(l)
    return "\n".join(out)


# a dash-label wrapped onto a second bold line:
#   "**Mr. S Kapoor - General Manager (...), State**\n**Bank of India**"
_WRAP_JOIN = re.compile(
    r"(\*\*\s*[-–]?\s*" + _HON + r"\.?\s+[^*\n]*?[–—-][^*\n]*?)\*\*[ \t]*\n"
    r"\*\*(?![-–—]|" + _HON + r"\.?\s)([^*\n:–—]{2,60})\*\*(?=\s*$)", re.M)


_BADLABEL = ("page", "note", "sub", "for", "management", "encl", "dear",
             "investor relations", "company secretary", "security", "scrip", "regd",
             "final transcript", "transcript", "call participant", "corporate participant",
             "presentation", "meeting", "duration", "webinar", "trading symbol", "symbol",
             "question", "answer", "event")
# company-name tokens â€” a label containing these is an entity, not a person
_COMPANY_WORD = re.compile(r'\b(limited|ltd|inc|llp|private|pvt|corporation|industries|'
                           r'solutions|technologies|holdings|enterprises|company)\b', re.I)


def _looks_like_name(name: str) -> bool:
    """Reject codes/headings like CIN, BSE, ISIN, SYNERGY GREEN INDUSTRIES."""
    if name.lower().startswith(_BADLABEL):
        return False
    if _COMPANY_WORD.search(name):       # company/entity, not a person
        return False
    words = name.split()
    if not (1 <= len(words) <= 5):
        return False
    # single all-caps/short token (CIN, BSE, NSE, ISIN) = not a person
    if len(words) == 1 and (name.isupper() or len(name) <= 4):
        return False
    # all-caps multi-word headings (PRESENTATION LINK, MEETING AUDIO) = not a speaker.
    # NOTE: this also rejects broker-hosted calls that render names in ALL CAPS
    # (e.g. AURUM). Those get flagged parse_quality=weak rather than mis-parsed â€”
    # allowing all-caps names caused worse false positives across the corpus.
    if name.isupper():
        return False
    return True


def _dash_label(ln: str):
    """'**Mr. X – Role, Firm**' -> 'X (Role, Firm)' if the role part is credible."""
    m = DASH_RE.match(ln)
    if not m:
        return None
    hon = m.group(1)
    name = " ".join(m.group(2).split())
    role = re.sub(r"\([^)]*\)", "", m.group(3))       # keep clean_name's (...) strip safe
    role = " ".join(role.split()).strip(" ,.-")
    if not _looks_like_name(name):
        return None
    credible = bool(hon) or _ROLEWORD.search(role) or is_operator(role)
    if not credible and len(name.split()) >= 2 and not re.search(r"\d", role):
        credible = bool(_FIRM_NAME.fullmatch(role))   # bare firm, e.g. "Motilal Oswal"
    if not credible:
        return None
    return f"{name} ({role})" if role else name


def parse_speaker_line(ln: str, allow_plain: bool = False):
    """Return (name, inline_text) if the line begins with a speaker label, else None.
    Requires markdown markup (* or leading #) to avoid matching body sentences;
    allow_plain additionally accepts a bare 'Name:' alone on a line (fallback pass)."""
    has_markup = ("*" in ln) or ln.lstrip().startswith("#")
    if not has_markup:
        if allow_plain:
            m = PLAIN_RE.match(ln.strip())
            if m:
                name = " ".join(m.group(1).split())
                if (is_operator(name) or len(name.split()) >= 2) and _looks_like_name(name):
                    return name, ""
        return None
    m = TS_RE.match(ln)
    if m:
        name = " ".join(m.group(1).split())
        if _looks_like_name(name):
            return name, ""
    hit = _dash_label(ln)
    if hit:
        return hit, ""
    m = COLON_RE.match(ln)
    if m:
        name = " ".join(m.group(1).split())
        if _looks_like_name(name):
            return name, m.group(2).strip()
    m = HEADER_RE.match(ln)
    if m:
        name = " ".join(m.group(1).split())
        if _looks_like_name(name):
            return name, ""
    return None

# Operator intro patterns â†’ analyst name + firm
#   "the line of Ankur Rudra from JP Morgan"
#   "next question is from the line of X from Y"
#   "Ankur Rudra from JP Morgan"
INTRO_RES = [
    re.compile(r'line of\s+([A-Z][A-Za-z.\'\- ]{2,40}?)\s+from\s+([A-Z][A-Za-z.&\'\- ]{2,50})', re.I),
    re.compile(r'question (?:is )?from\s+([A-Z][A-Za-z.\'\- ]{2,40}?)\s+(?:from|of)\s+([A-Z][A-Za-z.&\'\- ]{2,50})', re.I),
]

MONTHS = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
          "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}


def clean_firm(firm: str) -> str:
    """Trim operator boilerplate from an extracted firm name."""
    firm = re.split(r'\.|\bplease\b|\bthank\b|\bgo ahead\b|\byou (?:can|may)\b',
                    firm, maxsplit=1, flags=re.I)[0]
    return " ".join(firm.split()).strip(" .,-")


def parse_period(fname: str) -> dict:
    """'Apr_2022_transcript' -> {month: 4, year: 2022, label: 'Apr 2022'}."""
    m = re.search(r'([A-Za-z]{3})_?(20\d{2})', fname)
    if not m:
        return {"month": None, "year": None, "label": ""}
    mon = MONTHS.get(m.group(1).lower())
    return {"month": mon, "year": int(m.group(2)), "label": f"{m.group(1)} {m.group(2)}"}


def _raw_split(md: str, allow_plain: bool = False) -> list[dict]:
    """Split markdown into [{speaker, text}] using line-start speaker labels."""
    turns = []
    cur_speaker, cur_text = None, []

    def flush():
        if cur_speaker is not None:
            txt = " ".join(" ".join(cur_text).split()).strip()
            if txt:
                turns.append({"speaker": cur_speaker, "text": txt})

    for ln in md.splitlines():
        hit = parse_speaker_line(ln, allow_plain=allow_plain)
        if hit:
            flush()
            cur_speaker, inline = hit
            cur_text = [inline] if inline else []
        else:
            if cur_speaker is not None:
                cur_text.append(ln)
    flush()
    return turns


def split_turns(md: str, allow_plain: bool = False) -> list[dict]:
    """Split markdown into speaker turns. Two passes:
      1. line-start labels (## **Name**, **Name:** at line start)
      2. re-split on EMBEDDED **Name:** labels, but ONLY for speakers already
         confirmed in pass 1 â€” so '**Dhiral Shah:**' mid-sentence becomes a new
         turn, while a bolded term like '**Revenue:**' is left alone.
    This fixes pymupdf collapsing a back-and-forth onto one line."""
    turns = _raw_split(md, allow_plain=allow_plain)
    speakers = {t["speaker"] for t in turns}
    if not speakers:
        return turns
    # alternation of confirmed speaker display-names (longest first to avoid partial)
    alts = "|".join(sorted((re.escape(s) for s in speakers), key=len, reverse=True))
    embedded = re.compile(r'(?<!\n)\s*(\*\*\s*(?:' + alts + r')\s*:\s*\*\*)')
    md2 = embedded.sub(r'\n\1', md)
    return _raw_split(md2, allow_plain=allow_plain) if md2 != md else turns


def is_operator(speaker: str) -> bool:
    return any(w in speaker.lower() for w in OPERATOR_WORDS)


def clean_name(speaker: str) -> str:
    """Strip honorifics and role-parentheticals: 'Mr. Muthukumar (Moderator)' -> 'Muthukumar'."""
    s = re.sub(r'\([^)]*\)', '', speaker)                       # drop (Moderator) etc.
    s = re.sub(r'^\s*(mrs|mr|ms|dr|shri|smt|sh)\.?\s+', '', s, flags=re.I)
    return " ".join(s.split()).strip(" .,-")


# Strong cues that the Q&A floor is opening (avoid generic "you may ask questions later")
STRONG_QA_CUE = re.compile(
    r'first question|line of|begin the question|question[\- ]and[\- ]answer session|'
    r'open the (?:floor|line)|take (?:the )?(?:first )?question|queue for question',
    re.I)
MGMT_MONOLOGUE_CHARS = 350   # a turn this long in the opening = management prepared remark


def build_roster(turns: list[dict]) -> dict:
    """Map analyst-name -> firm, from operator intro lines (if any operator exists)."""
    roster = {}
    for t in turns:
        if is_operator(t["speaker"]):
            for rx in INTRO_RES:
                for m in rx.finditer(t["text"]):
                    name = " ".join(m.group(1).split()).strip(" .")
                    firm = clean_firm(m.group(2))
                    if 1 <= len(name.split()) <= 4 and firm:
                        roster[name.lower()] = firm
    return roster


MIN_QUESTION_CHARS = 30   # below this a "question" is an ack fragment ("Yes.", "Thank you")


def management_set(turns: list[dict], roster: dict | None = None) -> set:
    """Management = speakers who deliver a long monologue early in the call.
    Excludes anyone the operator introduced as an analyst (in roster) â€” this stops
    an analyst who asks a long question early from being mistaken for management."""
    roster = roster or {}
    cutoff = max(3, int(len(turns) * 0.4))
    mgmt = set()
    for t in turns[:cutoff]:
        if is_operator(t["speaker"]) or len(t["text"]) < MGMT_MONOLOGUE_CHARS:
            continue
        if clean_name(t["speaker"]).lower() in roster:   # known analyst â€” not management
            continue
        mgmt.add(t["speaker"])
    return mgmt


def find_qa_start(turns: list[dict], mgmt: set) -> int | None:
    """Q&A begins at the first non-management, non-operator speaker that appears
    after at least one management turn. Strong textual cues refine this."""
    seen_mgmt = False
    cue_idx = None
    for i, t in enumerate(turns):
        if t["speaker"] in mgmt:
            seen_mgmt = True
        if cue_idx is None and STRONG_QA_CUE.search(t["text"]):
            cue_idx = i
        # first real questioner: not mgmt, not operator, after some mgmt remarks
        if seen_mgmt and not is_operator(t["speaker"]) and t["speaker"] not in mgmt:
            return cue_idx if (cue_idx is not None and cue_idx <= i) else i
    return None


def _captured(turns: list[dict]) -> int:
    return sum(len(t["text"]) for t in turns)


def parse_transcript(md: str, symbol: str, period: dict) -> dict:
    md = strip_page_headers(md)
    md = _WRAP_JOIN.sub(r"\1 \2**", md)
    turns = split_turns(md)
    # fallback: markup-based labels captured almost nothing -> try plain 'Name:' lines
    if len(turns) < 8 or _captured(turns) < 0.3 * len(md):
        alt = split_turns(md, allow_plain=True)
        if _captured(alt) > _captured(turns) * 1.2 and len(alt) > len(turns):
            turns = alt
    roster = build_roster(turns)
    mgmt = management_set(turns, roster)

    # Promote speakers who give multiple long replies but were missed early
    # (handles execs who only speak in Q&A, e.g. a CEO who answers but didn't open).
    from collections import Counter
    long_replies = Counter(t["speaker"] for t in turns
                           if not is_operator(t["speaker"]) and len(t["text"]) >= MGMT_MONOLOGUE_CHARS)
    for spk, cnt in long_replies.items():
        if cnt >= 2 and spk.lower() not in roster:   # not a known analyst
            mgmt.add(spk)

    qa_start = find_qa_start(turns, mgmt)

    prepared = turns[:qa_start] if qa_start is not None else turns
    qa_turns = turns[qa_start:] if qa_start is not None else []

    prepared_chunks = [
        {"speaker": t["speaker"], "text": t["text"]}
        for t in prepared if not is_operator(t["speaker"]) and len(t["text"]) > 80
    ]

    def firm_for(speaker: str) -> str:
        s = clean_name(speaker).lower()
        if s in roster:
            return roster[s]
        for nm, fm in roster.items():
            if nm and (nm in s or s in nm):
                return fm
        # dash-label role: "X (Fund Manager, Nirmal Bang)" -> "Nirmal Bang";
        # "X (Motilal Oswal)" -> "Motilal Oswal" (bare firm, no role words)
        m = re.search(r"\(([^)]+)\)\s*$", speaker)
        if m:
            role = m.group(1).strip()
            if "," in role:
                return role.rsplit(",", 1)[1].strip()
            if not is_operator(role) and _FIRM_NAME.fullmatch(role):
                return role
        return ""

    # group Q&A into exchanges
    exchanges = []
    i, seq = 0, 0
    n = len(qa_turns)
    while i < n:
        t = qa_turns[i]
        is_question = (not is_operator(t["speaker"])) and (t["speaker"] not in mgmt)
        if is_question:
            seq += 1
            question = t["text"]
            analyst = clean_name(t["speaker"])
            firm = firm_for(t["speaker"])
            answers = []
            j = i + 1
            while j < n:
                tj = qa_turns[j]
                if is_operator(tj["speaker"]):
                    if STRONG_QA_CUE.search(tj["text"]) or re.search(r'\bnext\b', tj["text"], re.I):
                        break
                    j += 1
                    continue
                if tj["speaker"] in mgmt:
                    answers.append({"speaker": tj["speaker"], "text": tj["text"]})
                    j += 1
                else:
                    break  # next analyst's turn
            # drop trivial acknowledgement fragments ("Yes.", "Thank you.")
            substantive = len(question) >= MIN_QUESTION_CHARS or "?" in question
            if answers and substantive:
                responders = list(dict.fromkeys(a["speaker"] for a in answers))
                exchanges.append({
                    "seq": seq, "analyst": analyst, "firm": firm,
                    "question": question, "answers": answers, "responders": responders,
                })
            i = j
        else:
            i += 1

    return {
        "symbol": symbol,
        "period": period["label"],
        "year": period["year"],
        "month": period["month"],
        "n_turns": len(turns),
        "management": sorted(mgmt),
        "analyst_roster": roster,
        "prepared_remarks": prepared_chunks,
        "exchanges": exchanges,
        "has_qa": len(exchanges) > 0,
    }


def assess_quality(parsed: dict, md: str) -> dict:
    """Decide if the rule-based parse is trustworthy. Returns {quality, reasons}.
    Computed from the parse alone â€” no ground truth. Biased toward recall: a false
    'weak' only costs one cheap LLM call, while a missed bad parse poisons the data.

    The reliable signal is exchange COUNT relative to transcript length. Both real
    failure modes â€” under-segmentation and total failure â€” show up as too-few
    exchanges. (Markup-leak / giant-answer signals were tried and rejected: they
    fire on clean transcripts too, because pymupdf leaves benign inline markup.)"""
    ex = parsed["exchanges"]
    kb = len(md) / 1000
    reasons = []

    # presentation-style calls (RIL media calls: monologues, no analyst Q&A) parse
    # into person-attributed prepared remarks covering most of the source. That is
    # a GOOD parse despite zero exchanges â€” don't flag it sparse.
    pr = parsed.get("prepared_remarks") or []
    captured = (sum(len(p["text"]) for p in pr)
                + sum(len(e["question"]) + sum(len(a["text"]) for a in e["answers"])
                      for e in ex))
    well_segmented = (len(pr) >= 3 and len({p["speaker"] for p in pr}) >= 2
                      and captured >= 0.5 * len(md))

    # 1. substantial transcript that yielded almost no Q&A â†’ parser struggled
    if kb > 20 and len(ex) < 6 and not well_segmented:
        reasons.append(f"sparse({len(ex)}ex/{int(kb)}KB)")
    # 2. very long transcript with extremely thin coverage (genuine under-segmentation)
    elif kb > 60 and len(ex) < kb / 20 and not well_segmented:
        reasons.append(f"thin({len(ex)}ex/{int(kb)}KB)")
    # 3. zero exchanges despite an obvious Q&A session
    if not ex and STRONG_QA_CUE.search(md) and not well_segmented:
        reasons.append("zero_exchanges_with_qa_cue")
    # 4. no management speakers identified â†’ role detection failed
    if not parsed.get("management"):
        reasons.append("no_management")
    # 5. answers are mostly fragments/hand-offs (avg < 80 chars) â†’ mis-segmentation
    if ex:
        ans_lens = [len(a["text"]) for e in ex for a in e["answers"]]
        if ans_lens and sum(ans_lens) / len(ans_lens) < 80:
            reasons.append("fragmented_answers")
    # 6. conversion itself looks broken (almost no text from a real PDF)
    if kb < 3:
        reasons.append(f"thin_conversion({int(len(md))}chars)")

    quality = "weak" if reasons else "good"
    return {"quality": quality, "reasons": reasons}


def process_file(md_path: Path, symbol: str) -> dict:
    md = md_path.read_text(encoding="utf-8")
    period = parse_period(md_path.stem)
    parsed = parse_transcript(md, symbol, period)
    out_dir = OUT / symbol / "concalls"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{md_path.stem}.json"
    out_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    return parsed


def symbol_files(symbol: str) -> list[Path]:
    d = MD_DIR / symbol / "concalls"
    if not d.is_dir():
        return []
    return sorted(d.glob("*transcript*.md"))


def all_symbols() -> list[str]:
    return sorted([d.name for d in MD_DIR.iterdir() if d.is_dir()])


def run(symbols: list[str], show: bool):
    total_ex = total_files = 0
    for sym in symbols:
        for md_path in symbol_files(sym):
            parsed = process_file(md_path, sym)
            total_files += 1
            total_ex += len(parsed["exchanges"])
            if show:
                print(f"\n{sym} {parsed['period']}: {len(parsed['exchanges'])} exchanges, "
                      f"{len(parsed['prepared_remarks'])} prepared chunks, "
                      f"{len(parsed['analyst_roster'])} analysts")
                for ex in parsed["exchanges"][:2]:
                    print(f"  [{ex['seq']}] {ex['analyst']} ({ex['firm']}) -> {ex['responders']}")
                    print(f"      Q: {ex['question'][:120]}...")
    log.info(f"Parsed {total_files} transcripts, {total_ex:,} exchanges total")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--show", action="store_true", help="Print parsed summary")
    args = ap.parse_args()

    if args.symbol:
        run([args.symbol], args.show)
    elif args.all:
        run(all_symbols(), False)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
