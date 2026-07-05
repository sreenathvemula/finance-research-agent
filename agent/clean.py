"""Corpus-specific cleaning for RAG chunking (v2).

Rules derived from a 6-corpus audit (449 transcripts, 343 presentations, 24 annual
reports, 35+10k ratings, 94+14k announcements, 1000 xbrl files across 25 companies).
Each cleaner is deterministic — regex/heuristic only, no LLM.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# ===========================================================================
# shared text normalization
# ===========================================================================
_LIG = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi",
        "ﬄ": "ffl", "ﬆ": "st", "Ʃ": "tt"}

_PAGE_MARK = re.compile(r"##\s*\*\*\d{1,3}\*\*")
_PAGE_OF = re.compile(r"\bPage\s+\*{0,2}\d+\*{0,2}\s+of\s+\*{0,2}\d+\*{0,2}\b")
_INFY_FOOTER = re.compile(r"External Document\s*©?\s*20\d\d Infosys Limited\s*\d*")
_PUBLICATION_DISCLAIMER = re.compile(
    r"(?i)(republished with permission\.?\s*)?no part of this publication may be "
    r"reproduced or transmitted in any form or by any means"
    r"((?:(?!good (morning|afternoon|evening)|ladies and gentlemen|welcome to)[^.]){0,260})\.?")
_EDIT_NOTE = re.compile(r"\(This document has been (edited|transcribed)[^)]*\)")
_DSIGN = re.compile(r"Digitally signed by [A-Z][A-Za-z .]+(\s*Date:\s*20\d\d[.\d: +'@]*)?")
_ORDINAL = re.compile(r"\b(\d{1,3}) (st|nd|rd|th)\b")
_MD_HEAD = re.compile(r"(^|\n)\s*#{1,6}\s+")
_PUA = re.compile("[\uE000-\uF8FF]")   # private-use glyphs (bullets etc.)
_ZWSP = re.compile("[\u200b\u200c\u200d\ufeff\u00ad\ufffd]")
_WS = re.compile(r"[ \t]{2,}")


def _dropcap_merge(s: str) -> str:
    """Merge OCR drop-cap splits ('M ANAGEMENT' -> 'MANAGEMENT') — only when 3+
    such splits cluster together (signature-block/header artifact), never on
    isolated matches (avoids false positives on real two-token acronyms)."""
    matches = list(_DROPCAP.finditer(s))
    if len(matches) < 3:
        return s
    out, last = [], 0
    for m in matches:
        out.append(s[last:m.start()])
        out.append(m.group(1) + m.group(2))
        last = m.end()
    out.append(s[last:])
    return "".join(out)


def normalize_text(s: str) -> str:
    """Token-level cleanup safe for every corpus."""
    if not s:
        return ""
    s = s.replace(" Ɵ ", "ti").replace("Ɵ", "ti")   # Ɵ -> ti
    for k, v in _LIG.items():
        s = s.replace(k, v)
    s = s.replace("''", "'")
    s = _PUA.sub(" ", s)
    s = _ZWSP.sub("", s)
    s = re.sub(r"==> ?picture \[\d+ x \d+\] intentionally omitted <==", " ", s)
    # phase 1: patterns that require markdown decoration (**, ##) still present
    s = _PAGE_MARK.sub(" ", s)
    s = _PAGE_OF.sub(" ", s)
    s = _INFY_FOOTER.sub(" ", s)
    s = _PUBLICATION_DISCLAIMER.sub(" ", s)
    s = _EDIT_NOTE.sub(" ", s)
    s = _DSIGN.sub(" ", s)
    s = _ORDINAL.sub(r"\1\2", s)
    s = re.sub(r"\[(st|nd|rd|th)\]", r"\1", s)
    s = s.replace("**", "")
    s = _MD_HEAD.sub(r"\1", s)
    # phase 2: plain-text patterns (need ** / ## already stripped)
    s = _TITLE_PAGE.sub(" ", s)
    s = re.sub(r"(?i)\bEvent:\s*transcript of .{0,120}(call|meet)[^.]{0,60}", " ", s)
    s = _dropcap_merge(s)       # source wraps each drop-cap fragment in its own bold
                                 # markers ("**M** **ANAGEMENT**") — must run after ** strip
    s = _WS.sub(" ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ===========================================================================
# transcripts (JSON)
# ===========================================================================
_JUNK_ANALYST_EXACT = {"symbol", "analyst", "speaker", "thank you"}
_UNATTRIB = {"participant", "unidentified analyst", "unknown participant"}
_JUNK_ANALYST_SUB = ("transcript", "presentation link", "conference call",
                     "earnings call", "call participants", "corporate participants",
                     "listing", "relations section", "meeting video", "page no",
                     "trading symbol", "scrip", "final transcript")
_JUNK_SPEAKER = re.compile(
    r"(?i)\b(street|symbol|scrip|exchange|plaza|towers|complex|office|department|"
    r"listing|secretary|manager|email|website|transcript|conference|call participants|"
    r"disclaimer|unknown|bandra|kurla|phiroze|dalal)\b")
_COVER_TEXT = re.compile(
    r"(?i)(regd\.?\s*office|registered office|scrip code|bse limited|"
    r"national stock exchange|phiroze|dear sir|pursuant to regulation|"
    r"sebi \(listing|yours faithfully|kindly take (the same|this) on record|"
    r"compliance officer|encl\.?:?\s*as above|copy to:?\s|luxembourg stock exchange|"
    r"singapore exchange|shenton way|sgx centre|cin[-: ]l\d{5}|corporate identity number)")
_SIG_REPEAT = re.compile(r"[\s#*]*([A-Z]{3,})[\s#*]+([A-Z]{3,})[\s#*]+\1[\s#*]+\2\b")
_TITLE_PAGE = re.compile(
    r"(?i)\A\s*(final transcript\s*)?transcript of .{0,80}(earnings?|analyst|investor)"
    r"[^.]{0,120}(call|meet)[^.]{0,60}")
_DROPCAP = re.compile(r"\b([A-Z])\s+([A-Z]{2,})\b")
_JUNK_Q = re.compile(
    r"(?i)(company secretary|encl[:.]|yours faithfully|scrip code|dear sir|"
    r"hrs IST|compliance officer|digitally signed|regd\.?\s*office|bse limited|"
    r"national stock exchange)")
_URL_Q = re.compile(r"^\W*https?://\S+\s*$")
_MOD_TAIL = re.compile(
    r"(?i)(thank you\.?\s*)?(the\s+)?(next|last|final) question (is|comes) from"
    r"(?:\s+the line of)?.{0,120}$")
_CLOSING = re.compile(
    r"(?i)((ladies and gentlemen,?\s*)?on behalf of .{0,80}?concludes "
    r"(this|today'?s) (conference|call)|you may now disconnect).*$", re.S)
_FT_HDR = re.compile(r"(?i)(final transcript|republished with permission[^.]*\.)")


def _junk_analyst(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    if n in _JUNK_ANALYST_EXACT:
        return True
    return any(sub in n for sub in _JUNK_ANALYST_SUB)


def _junk_question(q: str) -> bool:
    q = (q or "").strip()
    if not q:
        return True
    if _URL_Q.match(q):
        return True
    if q.startswith("##") and len(q) < 150:
        return True
    return bool(_JUNK_Q.search(q[:400]))


def _answers_text(answers) -> str:
    parts = []
    for a in answers or []:
        if isinstance(a, dict):
            sp = (a.get("speaker") or "").strip()
            tx = (a.get("text") or "").strip()
            tx = _FT_HDR.sub(" ", tx) if sp.lower() == "final transcript" else tx
            if sp and sp.lower() != "final transcript" and not _JUNK_SPEAKER.search(sp):
                parts.append(f"{sp}: {tx}")
            else:
                parts.append(tx)
        else:
            parts.append(str(a))
    text = "\n".join(p for p in parts if p.strip())
    # moderator hand-off bled onto the answer tail
    tail = text[-250:]
    m = _MOD_TAIL.search(tail)
    if m:
        text = text[: len(text) - 250 + m.start()].rstrip()
    return text


_CONVO_START = re.compile(r"(?i)(good (morning|afternoon|evening)|ladies and gentlemen|"
                          r"welcome to|thank you[.,] (operator|and welcome))")


def _strip_cover_prefix(t: str) -> str:
    """Cut a leading SEBI cover-letter region, keeping any real content after it."""
    last = None
    for m in _COVER_TEXT.finditer(t[:2500]):
        last = m
    if not last:
        return t
    rest = t[last.end():]
    sig = _SIG_REPEAT.search(rest[:600])   # digital-signature name-repeat block
    if sig:
        rest = rest[sig.end():]
    m2 = _CONVO_START.search(rest[:2000])
    if m2:
        return rest[m2.start():]
    dot = rest.find(". ")
    return rest[dot + 2:] if 0 <= dot < 400 else rest


def clean_transcript(tj: dict) -> tuple[list[str], list[dict]]:
    """Return (prepared_remark_texts, exchanges) with junk filtered/salvaged.

    exchanges: [{"analyst": str|None, "firm": str|None, "q": str, "a": str}]
    """
    remarks: list[str] = []
    for r in tj.get("prepared_remarks") or []:
        sp = (r.get("speaker") or "").strip() if isinstance(r, dict) else ""
        tx = (r.get("text") or "").strip() if isinstance(r, dict) else str(r)
        if not tx:
            continue
        if sp and _JUNK_SPEAKER.search(sp):
            sp = ""                       # address fragment, not a person
            if _COVER_TEXT.search(tx[:1500]):
                tx = _strip_cover_prefix(tx)
                if len(tx) < 200:
                    continue
        elif _COVER_TEXT.search(tx[:1500]):
            tx = _strip_cover_prefix(tx)
            if len(tx) < 200:
                continue
        remarks.append(f"{sp}: {tx}" if sp else tx)

    exchanges: list[dict] = []
    for ex in tj.get("exchanges") or []:
        analyst = (ex.get("analyst") or "").strip()
        firm = (ex.get("firm") or "").strip()
        q = (ex.get("question") or "").strip()
        a = _answers_text(ex.get("answers"))
        junk = _junk_analyst(analyst) or _junk_question(q)
        if analyst.lower() in _UNATTRIB:
            junk = _junk_question(q)      # placeholder name but real Q
            analyst = ""
        if junk:
            if len(a) > 500:              # salvage: real opening remarks parsed as answer
                a = _strip_cover_prefix(a)
                if len(a) > 200:
                    remarks.append(a)
            continue
        exchanges.append({"analyst": analyst or None, "firm": firm or None,
                          "q": q, "a": a})
    if exchanges:                          # call-closing boilerplate on last answer
        exchanges[-1]["a"] = _CLOSING.sub("", exchanges[-1]["a"]).rstrip()
    return remarks, exchanges


def transcript_needs_md_fallback(tj: dict, json_size: int, md_size: int) -> bool:
    weak = tj.get("parse_quality") == "weak"
    empty = not (tj.get("exchanges") or [])
    tiny = md_size > 0 and json_size < 0.15 * md_size
    return (weak and (empty or tiny)) or (empty and tiny)


# ===========================================================================
# markdown letter-head cutting (presentations / annual reports)
# ===========================================================================
_LETTER_MARK = re.compile(r"(?i)(dear (sirs?|madam)|listing department|scrip code|"
                          r"phiroze jeejeebhoy|regulation 30|regd\.?\s*office)")
_LETTER_END = re.compile(r"(?i)(\bencl\b|yours (faithfully|sincerely)|company secretary|"
                         r"compliance officer|digitally signed|ACS ?\d{4,}|copy to|^cc:|"
                         r"^CIN[-: ])")
_CERT_LINE = re.compile(r"(DN: c=|\bo=Personal|serialNumber=|pseudonym=|postalCode=|"
                        r"\bcn=|2\.5\.4\.20=|^st=[A-Z])")
_HEX_LINE = re.compile(r"^[0-9a-f]{16,},?$")


def cut_cover_letter(lines: list[str], scan: int = 120) -> int:
    """Return index of first content line after the exchange cover letter (0 if none)."""
    head = lines[:scan]
    if not any(_LETTER_MARK.search(l) for l in head):
        return 0
    last = -1
    for i, l in enumerate(head):
        if _LETTER_END.search(l) or _CERT_LINE.search(l) or _HEX_LINE.match(l.strip()):
            last = i
    if last < 0:
        return 0
    i = last + 1
    while i < len(lines):
        s = lines[i].strip()
        if not s:                       # blank: content starts after
            i += 1
            break
        if len(s) >= 70 or s.startswith("## **"):
            break                        # new real content already
        i += 1
    return i


_LETTERHEAD_MARKERS = [
    re.compile(r"(?i)phiroze jeejeebhoy"),
    re.compile(r"(?i)exchange plaza"),
    re.compile(r"(?i)listing department"),
    re.compile(r"(?i)scrip code"),
    re.compile(r"(?i)regd\.?\s*office\s*:"),
    re.compile(r"(?i)yours faithfully"),
    re.compile(r"(?i)dear (sirs?|madam)"),
    re.compile(r"(?i)take (the )?(same|above|this|it)[^.]{0,40}record"),
    re.compile(r"(?i)pursuant to regulation"),
]


def count_letterhead_markers(text: str) -> int:
    """How many DISTINCT cover-letter markers a text contains."""
    return sum(1 for p in _LETTERHEAD_MARKERS if p.search(text))


# ===========================================================================
# presentations
# ===========================================================================
_PIC = re.compile(r"^\*{0,2}==> picture \[\d+ x \d+\] intentionally omitted <==\*{0,2}\s*$")
_PIC_MARK = re.compile(r"\*{0,2}-{3,}\s*(Start|End) of picture text\s*-{3,}\*{0,2}(<br>)?")
_COPYRIGHT = re.compile(r"^(\*\*\d{1,3}\*\* *)?(Copyright *)?© *20\d\d\b.{0,80}$")
_SLIDE_N = re.compile(r"^\**Slide \d+\**\s*$")
_GLYPH_ONLY = re.compile(r"^#{0,3}\s*\*\*[^\w]{1,4}\*\*\s*$")
_XREF = re.compile(r"^\*\*[^*]{0,60}: slides? \d+(-\d+)?\*\*\s*$", re.I)
_DASH_RULE = re.compile(r"^[-~ ]{25,}$")
_AXIS = re.compile(r"^\d{1,3}%?$")
_HEADING_LINE = re.compile(r"^(#{1,3}\s*)?\*\*.+\*\*\s*$")
_BOLD_INT = re.compile(r"^\*\*\d{1,3}\*\*\s*$")
_DISCLAIMER = re.compile(r"(?i)^(#{0,3}\s*)?\**\s*(legal\s+)?(disclaimer|safe harbou?r|"
                         r"forward[- ]looking statements?)\b")
_THANKYOU = re.compile(r"(?i)^#{0,3}\s*\*{0,2}thank ?you\*{0,2}\s*!?\s*$")
_CONTACT_HINT = re.compile(r"(?i)(@|\+91[- ]?\d|registered office|www\.|investor relations|design by)")


def clean_presentation(text: str) -> list[str]:
    """Return cleaned slide-level blocks (blank-line delimited, junk removed)."""
    if text.count(" \n") > len(text.splitlines()) // 3:   # dialect B
        text = "\n".join(l.rstrip() for l in text.splitlines())
        text = text.replace("<br>", " / ")
    text = _PIC_MARK.sub(" ", text)
    lines = text.splitlines()
    start = cut_cover_letter(lines)
    lines = lines[start:]

    out: list[str] = []
    prev_nonblank = ""
    axis_run: list[int] = []
    for ln in lines:
        s = ln.strip()
        if (_PIC.match(s) or _COPYRIGHT.match(s) or _SLIDE_N.match(s)
                or _GLYPH_ONLY.match(s) or _XREF.match(s) or _DASH_RULE.match(s)
                or _CERT_LINE.search(s) or _HEX_LINE.match(s)):
            continue
        if _AXIS.match(s):               # collect potential axis-tick runs
            axis_run.append(len(out))
        else:
            if len(axis_run) >= 4:       # drop the run
                for idx in reversed(axis_run):
                    if idx < len(out):
                        out.pop(idx)
            axis_run = []
        if _BOLD_INT.match(s) and _HEADING_LINE.match(prev_nonblank):
            continue                     # page number under slide title
        out.append(ln)
        if s:
            prev_nonblank = s

    blocks, cur = [], []
    for ln in out:
        if ln.strip():
            cur.append(ln)
        elif cur:
            blocks.append("\n".join(cur))
            cur = []
    if cur:
        blocks.append("\n".join(cur))

    cleaned: list[str] = []
    n = len(blocks)
    for bi, b in enumerate(blocks):
        first_lines = [l for l in b.splitlines()[:2]]
        if any(_DISCLAIMER.search(l) for l in first_lines) and len(b.splitlines()) > 4:
            continue
        if count_letterhead_markers(b) >= 2:   # cover letter that dodged the head cut
            continue
        tail = bi >= int(n * 0.85)
        if tail and any(_THANKYOU.match(l.strip()) for l in b.splitlines()):
            break
        if tail and len(_CONTACT_HINT.findall(b)) >= 2 and not re.search(r"\d{4,}", b):
            continue
        nb = normalize_text(b)
        if nb and len(nb) > 30:
            cleaned.append(nb)
    return cleaned


# ===========================================================================
# annual reports
# ===========================================================================
_NUMERIC_LINE = re.compile(r"^[\*\s]*[-–—()0-9,.%|]+[\*\s]*$")
_SECTION_WL = re.compile(
    r"(?i)\b(notice|directors.? report|board.?s report|management discussion|"
    r"corporate governance|business responsibility|BRSR|independent auditor.?s report|"
    r"balance sheet|statement of profit and loss|statement of cash flows|"
    r"statement of changes in equity|notes (to|forming part of)|"
    r"standalone financial statements|consolidated financial statements|"
    r"chairman|message from the|letter to shareholders)\b")
_TAB_LABEL = {"integrated report", "integrated reports", "statutory", "reports",
              "statutory reports", "financial statements", "corporate overview", "notice"}
_AR_JUNK_LINE = re.compile(r"(?i)(^GRI \d|^\*\*Designed by|^\*\*© \d{4}|no postage|"
                           r"if posted in|evoting@nsdl|helpdesk\.evoting|^\d{1,3}\s+of\s+\d{1,3}$)")


def clean_annual_report(text: str) -> list[tuple[str, str]]:
    """Return [(section_label, paragraph_block)] with furniture removed."""
    lines = text.replace("\r\n", "\n").splitlines()
    start = cut_cover_letter(lines, scan=300)
    lines = lines[start:]

    # frequency histogram for running headers (short heading/bold-only lines)
    freq: dict[str, int] = {}
    for ln in lines:
        s = ln.strip()
        if s and len(s) < 90 and (s.startswith("#") or _HEADING_LINE.match(s)):
            freq[s] = freq.get(s, 0) + 1
    seen_hdr: set[str] = set()

    kept: list[str] = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        # numeric table runs -> keep only a title stub
        if _NUMERIC_LINE.match(s) and s:
            j = i
            run = 0
            while j < len(lines) and lines[j].strip():
                if _NUMERIC_LINE.match(lines[j].strip()):
                    run += 1
                j += 1
            span = j - i
            if span >= 15 and run / max(1, span) >= 0.6:
                i = j
                continue
        if s in freq and freq[s] >= 8:
            if s in seen_hdr:
                i += 1
                continue
            seen_hdr.add(s)
        if s.lower().strip("* ") in _TAB_LABEL and (i == 0 or not lines[i - 1].strip()):
            i += 1
            continue
        if _AR_JUNK_LINE.search(s) or _PAGE_OF.search(s):
            i += 1
            continue
        if re.match(r"^\*{0,2}\d{1,4}\*{0,2}$", s) and (
                i + 1 >= len(lines) or not lines[i + 1].strip()
                or (i > 0 and not lines[i - 1].strip())):
            i += 1
            continue
        if re.match(r"^#{1,2} (?![A-Z*])", lines[i]):   # footnote as heading
            kept.append("Footnote: " + s.lstrip("# "))
            i += 1
            continue
        kept.append(lines[i])
        i += 1

    # paragraphs: blank lines are PAGE breaks — join unless sentence-final
    out: list[tuple[str, str]] = []
    section = ""
    cur: list[str] = []

    def flush():
        nonlocal cur
        if cur:
            block = normalize_text(" ".join(cur))
            if block and len(block) > 60:
                out.append((section, block))
            cur = []

    for ln in kept:
        s = ln.strip()
        if not s:
            if cur and re.search(r"[.!?:”\"]$", cur[-1].strip()):
                flush()
            continue
        if len(s) < 90 and (s.startswith("#") or _HEADING_LINE.match(s)):
            m = _SECTION_WL.search(s)
            if m:
                flush()
                section = re.sub(r"[#*]", "", s).strip()[:80]
                continue
        cur.append(s)
    flush()
    return out


# ===========================================================================
# credit ratings
# ===========================================================================
_MONTHS_FULL = ("January|February|March|April|May|June|July|August|September|"
                "October|November|December")
_DATE_PATTERNS = [
    re.compile(rf"(?P<m>{_MONTHS_FULL})\.?\s*(?P<d>\d{{1,2}})\s*(\[?(st|nd|rd|th)\]?)?\s*,?\s*(?P<y>\d{{4}})", re.I),
    re.compile(rf"(?P<d>\d{{1,2}})\s+(?P<m>{_MONTHS_FULL})\s+(?P<y>\d{{4}})", re.I),
    re.compile(r"(?P<d>\d{1,2})(?P<m>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?P<y>\d{4})"),
]
_MON_NUM = {m[:3].lower(): i + 1 for i, m in enumerate(_MONTHS_FULL.split("|"))}
_ANNEX = re.compile(r"(?i)^(##\s*)?\**\s*(annexure|rating\s*history\s*for|"
                    r"complexity\s*level\s*of|status\s*of\s*non[- ]?cooperation)")
_RATING_JUNK = re.compile(
    r"(?i)(^\**note:?\**\s*_?none of the directors|^1 crore = 10 million|"
    r"^refer to annexure for details|^_?\**source: |^(## )?\*{0,2}press release\*{0,2}\s*$|"
    r"^>? ?1complete definitions? of|^# ?please refer to the bwr website|"
    r"^_?source: india ratings & research|^primary analyst: |"
    r"^× (info|success|warning|error)!$|^(info|success|warning|error) alert$|"
    r"www\.acuite\.in|acuit.{0,3} rat|^##\s*$)")
_CLICK = re.compile(r"(?i)click ?here")
_CONTACT_HEAD = re.compile(r"(?i)^(## )?\**\s*(analyst contact|contact us|"
                           r"name and contact details of the rating analyst)")
_AGENCY_MAIL = re.compile(r"@(icraindia|icra|careratings|careedge|crisil|infomerics|"
                          r"brickworkratings|acuite|smera)\.(com|in)", re.I)
_INC_PARA = re.compile(r"(?i)as part of its process and in accordance with its rating "
                       r"agreement with.{0,2000}?best available information\.?", re.S)


def _squash(s: str) -> str:
    return re.sub(r"[\s*#]+", "", s).lower()


def clean_credit_rating(text: str) -> tuple[int, str, str]:
    """Return (date_int, quality_tag, cleaned_text). Empty text => skip file."""
    lines = text.replace("\r\n", "\n").splitlines()

    # date from first 8 non-empty lines
    date_int = 0
    seen = 0
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        seen += 1
        for pat in _DATE_PATTERNS:
            m = pat.search(s)
            if m:
                mon = _MON_NUM.get(m.group("m")[:3].lower())
                if mon:
                    date_int = int(m.group("y")) * 10000 + mon * 100 + int(m.group("d"))
                    break
        if date_int or seen >= 8:
            break

    if re.search(r"(?i)this credit bulletin is published solely", text) or \
            (lines and _squash(lines[0]) == "creditbulletin"):
        return date_int, "facility_update_stub", ""

    quality = ""
    if re.search(r"(?i)issuer not cooperating|\(INC\)", text[:1200]):
        quality = "issuer_not_cooperating"
        text = _INC_PARA.sub(" ", text)
        lines = text.splitlines()

    # content start: Rationale-family heading (whitespace-insensitive)
    start = 0
    for i, ln in enumerate(lines[:80]):
        sq = _squash(ln)
        if sq.startswith(("detailedrationale", "rationale", "rationaleforrating",
                          "keyratingdrivers")):
            start = i
            break
    body: list[str] = []
    contact_cut = False
    for ln in lines[start:]:
        s = ln.strip()
        if _ANNEX.match(s) or _squash(s).startswith(("annexure", "ratinghistoryfor",
                                                     "complexitylevelof")):
            break
        if _CONTACT_HEAD.match(s):
            contact_cut = True
            continue
        if contact_cut:
            if s.startswith("#"):
                contact_cut = False
            else:
                continue
        if _RATING_JUNK.search(s) or _PIC.match(s):
            continue
        if _CLICK.search(s) and "http" not in s:
            continue
        if _AGENCY_MAIL.search(s) or re.search(r"(^|\s)(Tel|Cell|Phone)\s*:|\+91[ -]?\d{5}", s):
            continue
        body.append(ln)
    cleaned = normalize_text("\n".join(body))
    return date_int, quality, cleaned


# ===========================================================================
# announcements — ONE chunk per file: subject + type + cleaned body
# ===========================================================================
_ANN_CLOSERS = re.compile(
    r"(?i)(this is for (your )?(information|records?)[^.]*\.|"
    r"(kindly|please) take (the )?(same|above|this|it)[^.]*records?[^.]*\.|"
    r"we request you to[^.]*record[^.]*\.|submitted for your[^.]*\.|"
    r"please acknowledge the receipt\.?|kindly acknowledge[^.]*\.|thank you,?)\s*$")
_ANN_SIG = re.compile(r"(?i)(yours faithfully|digitally signed by|phiroze jeejeebhoy|"
                      r"exchange plaza, plot no|dept\.? of corporate services)")
_ANN_REF = re.compile(r"(?i)^\s*(ref\.?\s*[:.][^\n]*|(dear\s+)?(sir|madam)s?\b[,/()\s]*)")
_ANN_BODY_CUT = re.compile(r"(?i)( please find| we wish| we enclose| in continuation|"
                           r" pursuant to| this is to inform)")
_DATE_VALID = re.compile(
    rf"(?i)((?P<m1>{_MONTHS_FULL})\.?\s+(?P<d1>\d{{1,2}})(st|nd|rd|th)?\s*,?\s*(?P<y1>20\d\d)|"
    rf"(?P<d2>\d{{1,2}})(st|nd|rd|th)?\s+(?P<m2>{_MONTHS_FULL})\s*,?\s*(?P<y2>20\d\d)|"
    r"(?P<d3>\d{1,2})[./-](?P<mo3>\d{1,2})[./-](?P<y3>20\d\d))")


def _ann_date_int(meta_date: str, body: str) -> int:
    for cand in (meta_date or "", body[:600]):
        m = _DATE_VALID.search(cand)
        if not m:
            continue
        g = m.groupdict()
        try:
            if g.get("y1"):
                d, mon, y = int(g["d1"]), _MON_NUM[g["m1"][:3].lower()], int(g["y1"])
            elif g.get("y2"):
                d, mon, y = int(g["d2"]), _MON_NUM[g["m2"][:3].lower()], int(g["y2"])
            else:
                d, mon, y = int(g["d3"]), int(g["mo3"]), int(g["y3"])
            if 1 <= d <= 31 and 1 <= mon <= 12 and 2000 <= y <= 2027:
                return y * 10000 + mon * 100 + d
        except (KeyError, ValueError):
            continue
    return 0


def parse_announcement(text: str, symbol: str) -> dict | None:
    subject, meta, body_lines = "", {}, []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("# ") and not subject:
            subject = s[2:].strip()
        elif s.startswith("- **Company:**"):
            for part in s[2:].split(" | "):
                m = re.match(r"\*\*([\w ]+):\*\*\s*(.*)", part.strip())
                if m:
                    meta[m.group(1).strip().lower()] = m.group(2).strip()
        elif s.startswith(("- **Filed by:**", "- **Ref:**")):
            continue
        elif s.startswith("_") and s.endswith("_"):
            continue                                   # italic footer boilerplate
        else:
            body_lines.append(s)

    subject = re.sub(r"^[-:.,\s]+", "", subject)
    if len(subject) >= 195:
        cut = _ANN_BODY_CUT.search(subject)
        dot = subject.find(". ")
        pos = min(p for p in (cut.start() if cut else 10 ** 6,
                              dot if dot > 20 else 10 ** 6))
        if pos < 10 ** 6:
            subject = subject[:pos].strip()

    body = " ".join(body_lines)
    sig = _ANN_SIG.search(body)
    if sig:
        body = body[:sig.start()]
    body = _ANN_REF.sub("", body)
    prev = None
    while prev != body:
        prev = body
        body = _ANN_CLOSERS.sub("", body.strip())
    body = normalize_text(body)
    if subject.lower()[:60] in body.lower()[:400]:
        pass                                            # H1 duplicated at body head; fine
    if len(body) < 40:
        body = ""
    ann_type = meta.get("type", "")
    date_int = _ann_date_int(meta.get("date", ""), body or subject)

    content = normalize_text(subject)
    if not content and not body:
        return None
    return {
        "subject": content, "type": ann_type, "body": body, "date_int": date_int,
        "dedupe_key": hashlib.md5(
            re.sub(r"\s+", " ", (content + body).lower()).encode()).hexdigest(),
    }
