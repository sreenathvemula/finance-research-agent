#!/usr/bin/env python3
"""
25_credit_ratings_to_md.py â€” Convert credit-rating documents to clean Markdown.

Credit ratings arrive in two physical forms (see survey):
  * PDF   : CARE, Infomerics, Acuite/SMERA, Brickwork, some CRISIL
  * HTML  : CRISIL (rich, real tables), ICRA + Fitch/IndRa (DEAD shells)

ICRA HTML is a login-walled portal shell; Fitch HTML is a JS "Loading..." shell.
Both carry no rationale text, so they are detected and SKIPPED (flagged), not
converted.  Everything else becomes Markdown with the agency boilerplate
(Contacts / About <agency> / Disclaimer / Criteria links / Privacy / Media note)
stripped, while the rated-company content (About the Company, Annexures, Rating
History, Financials) is kept.

Output : data/markdown/{SYMBOL}/credit_ratings/*.md      (mirrors PDF tree)
Flagged: data/parsed/_cr_flagged.csv                      (dead/empty inputs)

Usage:
  python 25_credit_ratings_to_md.py --sample 20      # review run -> data/_cr_review/
  python 25_credit_ratings_to_md.py --symbol GUJTHEM
  python 25_credit_ratings_to_md.py --all --workers 6
  python 25_credit_ratings_to_md.py --status
"""
import argparse, csv, logging, re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT     = Path(__file__).parent.parent
PDFS     = ROOT / "data" / "pdfs"
MD_DIR   = ROOT / "data" / "companies"
REVIEW   = ROOT / "data" / "_cr_review"
PARSED   = ROOT / "data" / "parsed"
FLAGGED  = PARSED / "_cr_flagged.csv"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("cr2md")

MIN_CONTENT = 400      # below this the converted doc is treated as empty/dead

# â”€â”€ agency from filename (..._from_<agency>.<ext>) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def agency_of(path: Path) -> str:
    m = re.search(r'from_([a-z0-9]+)$', path.stem, re.I)
    return m.group(1).lower() if m else "other"

# â”€â”€ boilerplate section markers (case-insensitive, tested on the whole line) â”€â”€
# A line that *equals* one of these (after stripping markdown #/*/: wrappers)
# opens a boilerplate block that is dropped until a CONTENT_RESUME line appears.
# Tested on every line (not just headings) because CRISIL-HTML section titles are
# plain Title-Case lines, not markdown headings.
_BOILER = re.compile(r"""^(
      contacts?(\s+us)?
    | (media|analyst|analytical|relationship|rating\s+desk)\s+contacts?
    | media\s+relations?
    | customer\s+service\s+helpdesk
    | about\s+(us|care|crisil|acuit\w*|infomerics|brickwork|icra|india\s+ratings|fitch|smera)\b.*
    | about\s+\w[\w&'â€™.\- ]*\bratings?\b.*           # About CRISIL Ratings Ltd / About Brickwork Ratings
    | disclaimer.*
    | note\s+for\s+(the\s+)?(media|print|electronic).*
    | for\s+print\s+and\s+(digital|electronic).*
    | (crisil\s+)?privacy\s+notice.*
    | links?\s+to\s+related\s+criteria.*
    | criteria\s+details.*
    | hyperlink\s*/?\s*reference\s+to\s+applicable\s+criteria.*
    | applicable\s+criteria.*
    | note\s+on\s+complexity\s+levels?.*
    | complexity\s+levels?\s+of\s+the\s+instrument.*
    | for\s+(the\s+)?(more|further|detailed)\s+(information|rationale|details).*
    | connect\s+with\s+us.*
)\s*:?\s*$""", re.I | re.X)

# A boilerplate block ends only when one of these real-content headings appears
# (so short lines inside a contact block â€” names, phone numbers â€” stay dropped).
_CONTENT_RESUME = re.compile(r"""^(
      annexure
    | rating\s+history
    | (key\s+)?financ
    | key\s+financial\s+indicators?
    | about\s+the\s+(company|group|industry|bank)
    | about\s+(the\s+)?compan
    | company\s+profile
    | liquidity\b
    | rating\s+sensitivit
    | analytical\s+approach
    | outlook\b
    | key\s+rating\s+driver
    | key\s+strength
    | key\s+weakness
    | detailed\s+description
    | detailed\s+rationale
    | status\s+of\s+non.?cooperation
)""", re.I | re.X)

# footer junk: CRISIL print-to-PDF timestamps / file:/// / x/y page markers
_FOOTER = re.compile(
    r'^\s*(\d{1,2}/\d{1,2}/\d{2,4},?\s*\d{1,2}:\d{2}\s*(am|pm)?|file:///|\d+\s*/\s*\d+|'
    r'rating\s+rationale|original\s+template\d*|please\s+note:\s*this\s+advisory.*)\s*$', re.I)
_PAGENUM = re.compile(r'^\s*[-â€“]?\s*(page\s+)?\d{1,3}(\s+of\s+\d{1,3})?\s*[-â€“]?\s*$', re.I)

def _bare(line: str) -> str:
    """Normalise a line to its first meaningful label for marker matching:
    strip heading/quote markers, table pipes (take first non-empty cell) and bold."""
    s = re.sub(r'^[#>]+\s*', '', line.strip())
    if '|' in s:                                   # markdown table row -> first cell
        cells = [c for c in s.split('|') if c.strip()]
        s = cells[0] if cells else ''
    s = s.strip().strip('*').strip()
    return s.rstrip(':').strip()


def strip_boilerplate(md: str) -> str:
    """Drop agency boilerplate blocks and footer junk. A boilerplate marker opens a
    skip that runs until a real content heading resumes (handles mid-doc boilerplate
    in Infomerics/Brickwork where Annexures follow the Contacts/About sections)."""
    out, skip = [], False
    for ln in md.splitlines():
        if not ln.strip():
            if not skip:
                out.append(ln)                     # preserve paragraph spacing
            continue
        bare = _bare(ln)
        # page footers incl. ICRA "Page |3" / "Page **|4**" (pipes/markdown removed)
        flat = re.sub(r'[\s*#>|_â€“-]+', '', ln).lower()
        if re.fullmatch(r'(page)?\d{1,3}(of\d{1,3})?', flat):
            continue
        if _FOOTER.match(bare) or _PAGENUM.match(bare):
            continue
        if skip:
            if bare and _CONTENT_RESUME.match(bare):
                skip = False                       # content resumes â€” fall through to keep
            else:
                continue
        if bare and _BOILER.match(bare):
            skip = True
            continue
        out.append(ln)
    md = re.sub(r'\n{3,}', '\n\n', "\n".join(out))
    return md.strip()


# â”€â”€ PDF path: pymupdf4llm gives markdown headings + real tables â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def pdf_to_md(pdf: Path) -> str | None:
    import pymupdf4llm
    chunks = pymupdf4llm.to_markdown(str(pdf), ignore_images=True, ignore_graphics=True,
                                     page_chunks=True, show_progress=False)
    pages = [c.get("text", "") for c in chunks]
    if not pages:
        return None
    # strip recurring per-page header/footer lines (agency name, "Press Release")
    from collections import Counter
    n = max(1, len(pages))
    freq = Counter()
    for pg in pages:
        for s in {l.strip() for l in pg.splitlines() if 0 < len(l.strip()) <= 90}:
            freq[s] += 1
    boiler = {l for l, c in freq.items() if c >= max(3, n * 0.4)}
    kept = []
    for pg in pages:
        for l in pg.splitlines():
            if l.strip() in boiler:
                continue
            kept.append(l)
    return strip_boilerplate("\n".join(kept))


# â”€â”€ HTML path: detect dead shells, else reading-order text â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ICRA pages are login-walled portal shells; Fitch/IndRa pages are JS "Loading..."
# shells.  Both lack rationale text and are skipped.  CRISIL (and Acuite/BWR) HTML
# is nested layout-tables, so we extract READING-ORDER TEXT (block tags -> newlines)
# rather than reconstruct tables, which explode on the nesting.
_DEAD_MARKERS = ("loading...", "please log in", "existing subscriber",
                 "download report as guest", "go to login")
_BLOCK_TAGS = ["p", "div", "tr", "br", "h1", "h2", "h3", "h4", "h5", "li"]

def html_to_md(html: Path) -> str | None:
    from bs4 import BeautifulSoup
    raw = html.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(raw, "lxml")
    for t in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        t.decompose()
    probe = soup.get_text(" ", strip=True).lower()
    if len(probe) < MIN_CONTENT or any(m in probe for m in _DEAD_MARKERS):
        return None                                  # dead / login-walled / JS shell
    body = soup.body or soup
    for tag in body.find_all(_BLOCK_TAGS):           # block boundaries -> newlines
        tag.insert_before("\n"); tag.insert_after("\n")
    text = body.get_text(" ")
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    # dedup consecutive identical lines (nested tables repeat each cell's text)
    out, prev = [], None
    for ln in text.splitlines():
        s = ln.strip()
        if s and s == prev:
            continue
        out.append(s); prev = s
    return strip_boilerplate("\n".join(out))


def convert(src: Path) -> tuple[str, str]:
    try:
        md = html_to_md(src) if src.suffix.lower() == ".html" else pdf_to_md(src)
    except Exception as e:
        return ("fail", f"{src} :: {e}")
    if not md or len(md) < MIN_CONTENT:
        return ("dead", str(src))
    return ("ok", md)


def _worker(args):
    src_str, dst_str = args
    status, payload = convert(Path(src_str))
    if status == "ok":
        dst = Path(dst_str)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(payload, encoding="utf-8")
        return ("ok", dst_str)
    return (status, payload)


def gather(symbols, out_root: Path):
    jobs = []
    for sym in symbols:
        d = PDFS / sym / "credit_ratings"
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.suffix.lower() in (".pdf", ".html"):
                rel = f.relative_to(PDFS).with_suffix(".md")
                jobs.append((str(f), str(out_root / rel)))
    return jobs


def all_symbols():
    return sorted(d.name for d in PDFS.iterdir()
                  if d.is_dir() and (d / "credit_ratings").is_dir())


def run(symbols, out_root, workers):
    jobs = gather(symbols, out_root)
    if not jobs:
        log.info("No credit-rating files found."); return
    log.info(f"Converting {len(jobs):,} files -> {out_root} ...")
    tot = {"ok": 0, "dead": 0, "fail": 0}
    dead = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_worker, j) for j in jobs]
        for i, fut in enumerate(as_completed(futs), 1):
            st, payload = fut.result()
            tot[st] = tot.get(st, 0) + 1
            if st in ("dead", "fail"):
                dead.append((st, payload))
            if i % 500 == 0:
                log.info(f"  {i:,}/{len(jobs):,} â€” {tot}")
    if dead:
        PARSED.mkdir(parents=True, exist_ok=True)
        with FLAGGED.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["status", "path"]); w.writerows(dead)
    log.info(f"DONE â€” {tot}  (flagged {len(dead)} -> {FLAGGED.name})")


def status():
    n = sum(1 for _ in (MD_DIR).rglob("credit_ratings/*.md"))
    print(f"credit_ratings markdown files: {n:,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--sample", type=int, help="Convert N random files to data/_cr_review/ for inspection")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        status(); return

    if args.sample:
        import random
        syms = all_symbols()
        random.seed(7)
        random.shuffle(syms)
        # collect files across agencies until we have N, 1 per company
        picked, jobs = [], []
        for sym in syms:
            d = PDFS / sym / "credit_ratings"
            files = [f for f in d.iterdir() if f.suffix.lower() in (".pdf", ".html")]
            if files:
                picked.append(random.choice(files))
            if len(picked) >= args.sample:
                break
        for f in picked:
            rel = f.relative_to(PDFS).with_suffix(".md")
            jobs.append((str(f), str(REVIEW / rel)))
        log.info(f"Sample: {len(jobs)} files -> {REVIEW}")
        for j in jobs:
            st, payload = _worker(j)
            tag = agency_of(Path(j[0]))
            log.info(f"  [{st:4}] {tag:10} {Path(j[0]).parent.parent.name}/{Path(j[0]).name}")
        return

    out = MD_DIR
    syms = [args.symbol] if args.symbol else all_symbols()
    run(syms, out, args.workers)


if __name__ == "__main__":
    main()
