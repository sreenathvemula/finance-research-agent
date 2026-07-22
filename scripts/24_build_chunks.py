#!/usr/bin/env python3
"""
24_build_chunks.py â€” Turn parsed concalls into retrieval chunks for RAG.

Input:
  data/parsed/{SYMBOL}/concalls/*.json   (good = structured exchanges; weak = flagged)
  data/markdown/{SYMBOL}/concalls/*.md   (fallback text for weak transcripts + presentations)

Chunking rules:
  GOOD transcript  -> one chunk per Q&A exchange (question + its answers, kept together),
                      plus one chunk per management prepared-remark monologue.
                      Boundaries are KNOWN from the parse â€” no guessing.
  WEAK transcript  -> sliding-window chunks over the raw markdown, labelled parse_quality
                      "weak" so the LLM knows speaker attribution is unreliable.
  PRESENTATION     -> sliding-window chunks over the markdown (slide bullets/guidance).

Every chunk carries metadata + a plain-English `quality_note` so Claude can calibrate
trust at answer time. A deterministic context line is prepended for better retrieval
(no LLM needed).

Output:
  data/chunks/concalls.jsonl   (one JSON object per line)

Usage:
  python 24_build_chunks.py --symbol INFY        # one company (prints sample)
  python 24_build_chunks.py --all                # whole corpus -> jsonl
  python 24_build_chunks.py --stats              # summarise the jsonl
"""
import argparse, json, logging, re
from pathlib import Path

ROOT   = Path(__file__).parent.parent
PARSED = ROOT / "data" / "parsed"
MD_DIR = ROOT / "data" / "companies"
OUT    = ROOT / "data" / "chunks"
OUT.mkdir(parents=True, exist_ok=True)
JSONL  = OUT / "concalls.jsonl"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("chunk")

TARGET_TOK   = 800     # aim for chunks around this size
MAX_TOK      = 1100    # split anything bigger
OVERLAP_TOK  = 120     # sliding-window overlap for weak/presentation text


def approx_tok(s: str) -> int:
    return max(1, len(s) // 4)   # ~4 chars/token, good enough for sizing


def quarter_of(month: int | None) -> str | None:
    if not month:
        return None
    # Indian earnings calls: results announced ~1 month after quarter end
    return {1:"Q3",2:"Q3",4:"Q4",5:"Q4",7:"Q1",8:"Q1",10:"Q2",11:"Q2",
            3:"Q3",6:"Q4",9:"Q1",12:"Q2"}.get(month)


def ctx_line(symbol, period, section, analyst=None, firm=None):
    """Deterministic context prefix to improve retrieval (no LLM)."""
    base = f"{symbol} {period} earnings call"
    if section == "qa":
        who = f" â€” analyst {analyst}" + (f" ({firm})" if firm else "")
        return f"[{base}, Q&A{who}]"
    if section == "prepared_remarks":
        return f"[{base}, management remarks]"
    if section == "presentation":
        return f"[{symbol} {period} investor presentation]"
    return f"[{base}]"


_TITLE_WORD = re.compile(r'\b(officer|chief|head|director|controller|president|'
                         r'analyst|investor|cfo|ceo|coo|managing|financial|relations|'
                         r'vice|chairman|secretary)\b', re.I)


def is_roster_like(text: str) -> bool:
    """Participant-list fragment (names + titles), not real prepared remarks.
    Rosters are short, title-heavy, OR have very low sentence density (name lists)."""
    words = text.split()
    if len(text) < 160:                      # real monologues are long
        return True
    titles = len(_TITLE_WORD.findall(text))
    if titles >= 4 and titles / max(1, len(words)) > 0.12:
        return True
    # name-list: lots of words but almost no sentences (few terminating periods)
    sentences = len(re.findall(r'[.!?]\s', text))
    return len(words) > 25 and sentences / max(1, len(words)) < 0.02


def sentence_split(text: str) -> list[str]:
    return re.split(r'(?<=[.!?])\s+', text)


def _hard_split(s: str, cap=MAX_TOK) -> list[str]:
    """Force-split a single oversized run-on (no sentence breaks) on word boundaries."""
    if approx_tok(s) <= cap:
        return [s]
    words, out, cur = s.split(), [], []
    for w in words:
        cur.append(w)
        if approx_tok(" ".join(cur)) >= cap:
            out.append(" ".join(cur)); cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def window(text: str, target=TARGET_TOK, overlap=OVERLAP_TOK) -> list[str]:
    """Greedy sentence-packed sliding window; hard-splits oversized run-on sentences."""
    sents = [p for s in sentence_split(text) for p in _hard_split(s)]
    chunks, cur, cur_tok = [], [], 0
    for s in sents:
        st = approx_tok(s)
        if cur and cur_tok + st > target:
            chunks.append(" ".join(cur))
            # carry overlap
            back, btok = [], 0
            for x in reversed(cur):
                btok += approx_tok(x)
                back.insert(0, x)
                if btok >= overlap:
                    break
            cur, cur_tok = back[:], btok
        cur.append(s); cur_tok += st
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def emit(records, base, section, text, method, quality, note, extra=None):
    """Append one or more chunk records (splitting oversized text)."""
    pieces = [text] if approx_tok(text) <= MAX_TOK else window(text)
    for i, body in enumerate(pieces):
        rec = dict(base)
        rec.update({
            "section": section,
            "chunk_method": method,
            "parse_quality": quality,
            "quality_note": note,
            "text": body,
            "n_tokens": approx_tok(body),
            "chunk_id": f"{base['symbol']}_{base['period'].replace(' ','')}_{section}_{len(records)}",
        })
        if extra:
            rec.update(extra)
        records.append(rec)


WEAK_NOTE = ("Parser could not cleanly segment this call (parse_quality=weak); speaker "
             "attribution and Q&A boundaries may be unreliable â€” treat as raw transcript text.")
GOOD_QA_NOTE = "Structured Q&A exchange with verified speaker attribution."
PREP_NOTE = "Management prepared remarks (opening monologue)."
PRES_NOTE = "Investor-presentation text. Charts/tables are images and are NOT captured here; numbers come from structured fundamentals (Tijori/Screener)."


def chunks_for_transcript(pjson: Path, records: list):
    d = json.loads(pjson.read_text(encoding="utf-8"))
    sym, period = d["symbol"], d.get("period", "")
    base = {"symbol": sym, "period": period, "year": d.get("year"),
            "quarter": quarter_of(d.get("month")), "doc_type": "concall_transcript"}
    quality = d.get("parse_quality", "good")

    if quality == "weak" or not d.get("exchanges"):
        # fall back to sliding window over the markdown
        md = (MD_DIR / sym / "concalls" / f"{pjson.stem}.md")
        text = md.read_text(encoding="utf-8") if md.exists() else ""
        if not text:
            return
        for body in window(text):
            emit(records, base, "fulltext", f"{ctx_line(sym,period,'')}\n{body}",
                 "sliding_window", "weak", WEAK_NOTE)
        return

    # GOOD: prepared remarks (skip participant-roster fragments â€” short + title-heavy)
    for pr in d.get("prepared_remarks", []):
        if is_roster_like(pr["text"]):
            continue
        body = f"{ctx_line(sym,period,'prepared_remarks')}\n{pr['speaker']}: {pr['text']}"
        emit(records, base, "prepared_remarks", body, "prepared", "good", PREP_NOTE,
             extra={"speaker": pr["speaker"]})

    # GOOD: each Q&A exchange
    for ex in d["exchanges"]:
        ans = "\n".join(f"A ({a['speaker']}): {a['text']}" for a in ex["answers"])
        body = (f"{ctx_line(sym,period,'qa',ex['analyst'],ex['firm'])}\n"
                f"Q ({ex['analyst']}): {ex['question']}\n{ans}")
        emit(records, base, "qa", body, "exchange", "good", GOOD_QA_NOTE,
             extra={"analyst": ex["analyst"], "firm": ex["firm"],
                    "responders": ex["responders"], "seq": ex["seq"]})


def chunks_for_presentation(md_path: Path, records: list):
    sym = md_path.parent.parent.name
    period = (re.search(r'([A-Za-z]{3}_?20\d{2})', md_path.stem) or [None, ""])[1].replace("_", " ")
    text = md_path.read_text(encoding="utf-8")
    if len(text) < 200:
        return
    base = {"symbol": sym, "period": period, "year": None,
            "quarter": None, "doc_type": "concall_presentation"}
    for body in window(text):
        emit(records, base, "presentation", f"{ctx_line(sym,period,'presentation')}\n{body}",
             "sliding_window", "good", PRES_NOTE)


def build(symbols: list[str], write: bool):
    records = []
    for sym in symbols:
        pdir = PARSED / sym / "concalls"
        if pdir.is_dir():
            for pj in sorted(pdir.glob("*.json")):
                chunks_for_transcript(pj, records)
        mdir = MD_DIR / sym / "concalls"
        if mdir.is_dir():
            for md in sorted(mdir.glob("*presentation*.md")):
                chunks_for_presentation(md, records)
    if write:
        with JSONL.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        log.info(f"Wrote {len(records):,} chunks -> {JSONL}")
    return records


def all_symbols():
    s = set()
    if PARSED.exists():
        s |= {d.name for d in PARSED.iterdir() if d.is_dir()}
    return sorted(s)


def stats():
    if not JSONL.exists():
        print("No chunks yet."); return
    from collections import Counter
    rows = [json.loads(l) for l in JSONL.open(encoding="utf-8")]
    print(f"Total chunks: {len(rows):,}")
    for k in ("doc_type", "section", "parse_quality", "chunk_method"):
        print(f"  by {k}: {dict(Counter(r.get(k) for r in rows))}")
    toks = [r["n_tokens"] for r in rows]
    print(f"  tokens: min={min(toks)} median={sorted(toks)[len(toks)//2]} max={max(toks)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if args.stats:
        stats()
    elif args.symbol:
        recs = build([args.symbol], write=False)
        print(f"\n{len(recs)} chunks for {args.symbol}. Samples:\n")
        for r in recs[:3]:
            print(f"--- {r['chunk_id']} [{r['section']}/{r['parse_quality']}] {r['n_tokens']}tok")
            print(r["text"][:400], "\n")
    elif args.all:
        build(all_symbols(), write=True)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
