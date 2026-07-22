"""RAG layer: chunk every document corpus, embed on GPU, index into ChromaDB, search.

Document sources per company (data/companies/{SYM}/):
  concalls/*_transcript.json   -> structured Q&A chunks (one per exchange) + prepared remarks
  concalls/*_transcript.md     -> fallback sliding-window when no/weak JSON parse
  concalls/*_presentation.md   -> sliding-window
  annual_reports/*.md          -> sliding-window (date = fiscal year end)
  credit_ratings/*.md          -> sliding-window (date from filename when present)
  announcements/*.md           -> sliding-window
  xbrl/*.md                    -> sliding-window

Chunk ids are deterministic (relpath::seq) and a manifest of file mtimes makes
re-indexing incremental: only new/changed files are (re)embedded.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import (
    CHROMA_DIR, COLLECTION_NAME, COMPANIES, DOC_PREFIX, EMBED_BATCH,
    EMBED_MAX_SEQ_LENGTH, EMBED_MODEL, EMBED_TRUNCATE_DIM, MANIFEST_PATH, MONTHS,
    OVERLAP_TOKENS, QUERY_PREFIX, ROOT, TARGET_TOKENS, TARGET_TOKENS_MAX,
)
from .tokenization import get_tokenizer, tok_len

_embedder = None
_collection = None


# ------------------------------------------------------------- singletons ---
def get_embedder():
    global _embedder
    if _embedder is None:
        import torch
        from sentence_transformers import SentenceTransformer
        cuda_ok = torch.cuda.is_available()
        device = "cuda" if cuda_ok else "cpu"
        # explicit, unmissable — the old silent fallback made "was the GPU even
        # used?" impossible to answer after the fact from the log alone
        if cuda_ok:
            print(f"[embedder] device=cuda ({torch.cuda.get_device_name(0)})", flush=True)
        else:
            print("[embedder] WARNING: CUDA not available, falling back to CPU "
                 "— this will be dramatically slower", flush=True)
        _embedder = SentenceTransformer(
            EMBED_MODEL, device=device, trust_remote_code=True,
            truncate_dim=EMBED_TRUNCATE_DIM,
        )
        if cuda_ok:
            # measured on this GPU: fp16 vs fp32 embeddings are cosine-identical
            # (0.999999+) but fp16 runs 3.5x faster at the same batch size
            # (2164 vs 610 chunks/min) — the v3 rebuild was fp32-bound, not
            # logic-bound. Larger batch sizes were also measured and are
            # WORSE on this GPU (throughput drops as batch grows past 32),
            # so EMBED_BATCH is left at 32 deliberately, not bumped.
            _embedder = _embedder.half()
            print("[embedder] using fp16 (3.5x measured speedup, verified "
                  "cosine-identical to fp32)", flush=True)
        assert _embedder.max_seq_length >= EMBED_MAX_SEQ_LENGTH, (
            f"expected max_seq_length>={EMBED_MAX_SEQ_LENGTH}, "
            f"got {_embedder.max_seq_length}")
    return _embedder


def get_collection():
    global _collection
    if _collection is None:
        import chromadb
        from chromadb.config import Settings
        client = chromadb.PersistentClient(
            path=str(CHROMA_DIR), settings=Settings(anonymized_telemetry=False)
        )
        _collection = client.get_or_create_collection(
            COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
    return _collection


# ---------------------------------------------------------------- chunking --
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")


def _hard_split_words(text: str, max_tokens: int) -> list[str]:
    """Split a single oversized run-on (no sentence breaks) into pieces of at
    most max_tokens REAL tokens, by slicing token ids directly.

    Previously did this on Python .split() word boundaries, checking length
    every 8 words — but a whitespace-sparse blob (e.g. a malformed exploded
    numeric table, a run-together encoding artifact) can be ONE "word" to
    .split() while holding thousands of real tokens, so the check never
    fired at all (measured: a 21,000-real-token whitespace-free blob came
    back as a single unsplit "word", passing straight through to the
    embedder — this is what produced the 8,624-token outlier chunk found
    during the v3 rebuild). Token-id slicing has no such blind spot."""
    tok = get_tokenizer()
    ids = tok(text, add_special_tokens=False, truncation=False)["input_ids"]
    return [tok.decode(ids[i:i + max_tokens]) for i in range(0, len(ids), max_tokens)]


def sliding_chunks(text: str) -> list[str]:
    """Greedy sentence/paragraph packing to ~TARGET_TOKENS with OVERLAP_TOKENS
    overlap, measured in REAL model tokens (not a chars/4 approximation — that
    was the bug: it undercounted at the tail, silently truncating ~51% of
    chunks at embed time under the old 512-token model)."""
    text = text.strip()
    if not text:
        return []
    if tok_len(text) <= TARGET_TOKENS_MAX:
        return [text]
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    chunks, cur, cur_tok = [], [], 0
    for p in parts:
        ptok = tok_len(p)
        if ptok > TARGET_TOKENS_MAX:  # single huge run-on: hard split on words
            if cur:
                chunks.append(" ".join(cur))
                cur, cur_tok = [], 0
            chunks.extend(_hard_split_words(p, TARGET_TOKENS_MAX))
            continue
        if cur_tok + ptok > TARGET_TOKENS and cur:
            chunks.append(" ".join(cur))
            # overlap: carry tail parts worth ~OVERLAP_TOKENS
            tail, ttok = [], 0
            for q in reversed(cur):
                ttok += tok_len(q)
                tail.insert(0, q)
                if ttok >= OVERLAP_TOKENS:
                    break
            cur, cur_tok = tail, ttok
        cur.append(p)
        cur_tok += ptok
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def transcript_chunks(tj: dict, symbol: str, period: str) -> list[tuple[str, dict]]:
    """Structured chunks from a parsed transcript JSON: one per Q&A exchange + remarks.

    v2: junk exchanges/speakers filtered, letterhead removed, artifacts normalized
    (agent.clean rules from the corpus audit)."""
    from .clean import clean_transcript, normalize_text
    chunks: list[tuple[str, dict]] = []
    ctx = f"[{symbol} earnings call, {period}]"
    remarks, exchanges = clean_transcript(tj)
    remarks_text = normalize_text("\n".join(remarks))
    if remarks_text:
        for c in sliding_chunks(remarks_text):
            chunks.append((f"{ctx} Management prepared remarks:\n{c}",
                           {"section": "prepared_remarks"}))
    for ex in exchanges:
        who = ""
        if ex["analyst"]:
            who = f" (analyst: {ex['analyst']}{', ' + ex['firm'] if ex['firm'] else ''})"
        body = normalize_text(f"Q: {ex['q']}\nA: {ex['a']}")
        for c in sliding_chunks(f"{ctx} Q&A{who}\n{body}"):
            chunks.append((c, {"section": "qa"}))
    return chunks


def pack_blocks(blocks: list[str], ctx: str) -> list[str]:
    """Greedy-pack pre-cleaned blocks to ~TARGET_TOKENS; oversize blocks get windowed."""
    out, cur, cur_tok = [], [], 0
    for b in blocks:
        btok = tok_len(b)
        if btok > TARGET_TOKENS_MAX:
            if cur:
                out.append("\n\n".join(cur)); cur, cur_tok = [], 0
            out.extend(sliding_chunks(b))
            continue
        if cur_tok + btok > TARGET_TOKENS and cur:
            out.append("\n\n".join(cur)); cur, cur_tok = [], 0
        cur.append(b); cur_tok += btok
    if cur:
        out.append("\n\n".join(cur))
    return [f"{ctx}\n{c}" for c in out if c.strip()]


# ------------------------------------------------------------ date parsing --
def date_from_name(name: str, doc_type: str) -> tuple[int, str]:
    """Return (date_int yyyymmdd, period label). 0 when unknown."""
    if doc_type in ("concall_transcript", "concall_presentation"):
        m = re.match(r"([A-Z][a-z]{2})_(\d{4})", name)
        if m and m.group(1) in MONTHS:
            y, mo = int(m.group(2)), MONTHS[m.group(1)]
            return y * 10000 + mo * 100 + 1, f"{m.group(1)} {y}"
    if doc_type == "annual_report":
        m = re.search(r"(\d{4})", name)
        if m:
            y = int(m.group(1))
            return y * 10000 + 331, f"FY{y}"
    if doc_type == "credit_rating":
        m = re.search(r"(\d{1,2})_([A-Z][a-z]{2})_(\d{4})", name)
        if m and m.group(2) in MONTHS:
            return (int(m.group(3)) * 10000 + MONTHS[m.group(2)] * 100 + int(m.group(1)),
                    f"{m.group(1)} {m.group(2)} {m.group(3)}")
        m = re.search(r"(\d{1,2})_([A-Z][a-z]{2})", name)
        if m and m.group(2) in MONTHS:
            return 0, f"{m.group(1)} {m.group(2)}"
    return 0, ""


# ----------------------------------------------------------------- sources --
def iter_company_files(symbol: str):
    """Yield (path, doc_type) for every indexable document of one company."""
    base = COMPANIES / symbol
    for p in sorted((base / "concalls").glob("*_presentation.md")):
        yield p, "concall_presentation"
    for p in sorted((base / "concalls").glob("*_transcript.json")):
        yield p, "concall_transcript"
    # transcripts that only exist as markdown (no json parse)
    jsons = {p.stem for p in (base / "concalls").glob("*_transcript.json")}
    for p in sorted((base / "concalls").glob("*_transcript.md")):
        if p.stem not in jsons:
            yield p, "concall_transcript"
    for sub, dt in [("annual_reports", "annual_report"), ("credit_ratings", "credit_rating"),
                    ("announcements", "announcement")]:   # xbrl excluded (bad data — see config)
        for p in sorted((base / sub).glob("*.md")):
            yield p, dt


def file_chunks(path: Path, doc_type: str, symbol: str) -> list[tuple[str, dict]]:
    """Chunk one file -> [(text, extra_meta)] using the audit-driven v2 cleaners."""
    from . import clean
    date_int, period = date_from_name(path.stem, doc_type)
    base_meta = {"date_int": date_int, "period": period}

    if doc_type == "concall_transcript" and path.suffix == ".json":
        tj = json.loads(path.read_text(encoding="utf-8"))
        period = tj.get("period") or period
        if tj.get("year") and tj.get("month"):
            base_meta["date_int"] = tj["year"] * 10000 + tj["month"] * 100 + 1
        base_meta["period"] = period
        base_meta["quality"] = tj.get("parse_quality", "unknown")
        md = path.with_suffix(".md")
        md_size = md.stat().st_size if md.exists() else 0
        if not clean.transcript_needs_md_fallback(tj, path.stat().st_size, md_size):
            return [(t, {**base_meta, **m}) for t, m in transcript_chunks(tj, symbol, period)]
        # weak/content-lost parse: window the sibling markdown instead
        text = md.read_text(encoding="utf-8", errors="ignore") if md.exists() else ""
        lines = text.splitlines()
        text = clean.normalize_text("\n".join(lines[clean.cut_cover_letter(lines):]))
        ctx = f"[{symbol} earnings call, {period}; speaker attribution unreliable]"
        base_meta["quality"] = "weak"
        return [(f"{ctx}\n{c}", dict(base_meta)) for c in sliding_chunks(text)]

    text = path.read_text(encoding="utf-8", errors="ignore")

    if doc_type == "concall_transcript":            # .md with no JSON sibling
        lines = text.splitlines()
        text = clean.normalize_text("\n".join(lines[clean.cut_cover_letter(lines):]))
        ctx = f"[{symbol} earnings call, {period}; speaker attribution unreliable]"
        base_meta["quality"] = "weak"
        return [(f"{ctx}\n{c}", dict(base_meta)) for c in sliding_chunks(text)]

    if doc_type == "concall_presentation":
        blocks = clean.clean_presentation(text)
        ctx = f"[{symbol} concall presentation, {period}]"
        return [(c, dict(base_meta)) for c in pack_blocks(blocks, ctx)]

    if doc_type == "annual_report":
        sections = clean.clean_annual_report(text)
        chunks: list[tuple[str, dict]] = []
        cur_sec, cur_blocks = "", []
        for sec, block in sections + [("__end__", "")]:
            if sec != cur_sec and cur_blocks:
                ctx = f"[{symbol} annual report, {period}" + \
                      (f" — {cur_sec}]" if cur_sec else "]")
                for c in pack_blocks(cur_blocks, ctx):
                    chunks.append((c, {**base_meta, "section": cur_sec}))
                cur_blocks = []
            cur_sec = sec
            if block:
                cur_blocks.append(block)
        return chunks

    if doc_type == "credit_rating":
        d_int, quality, cleaned = clean.clean_credit_rating(text)
        if not cleaned or len(cleaned) < 120:
            return []                                # facility-update stub / empty
        if d_int:
            base_meta["date_int"] = d_int
            base_meta["period"] = f"{d_int % 100:02d}-{(d_int // 100) % 100:02d}-{d_int // 10000}"
        if quality:
            base_meta["quality"] = quality
        ctx = f"[{symbol} credit rating, {base_meta['period'] or 'undated'}]"
        return [(f"{ctx}\n{c}", dict(base_meta)) for c in sliding_chunks(cleaned)]

    if doc_type == "announcement":
        ann = clean.parse_announcement(text, symbol)
        if not ann:
            return []
        if ann["date_int"]:
            base_meta["date_int"] = ann["date_int"]
        base_meta["dedupe_key"] = ann["dedupe_key"]
        parts = [f"[{symbol} announcement]", ann["subject"]]
        if ann["type"]:
            parts.append(f"Type: {ann['type']}")
        if ann["body"]:
            parts.append(ann["body"])
        return [("\n".join(parts), dict(base_meta))]   # ONE chunk per file, never split

    label = doc_type.replace("_", " ")
    ctx = f"[{symbol} {label}{', ' + period if period else ''}]"
    return [(f"{ctx}\n{c}", dict(base_meta)) for c in sliding_chunks(clean.normalize_text(text))]


# ---------------------------------------------------------------- indexing --
def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {}


def _save_manifest(m: dict):
    MANIFEST_PATH.write_text(json.dumps(m), encoding="utf-8")


def index_symbols(symbols: list[str], progress=print, force: bool = False) -> dict:
    """Incrementally (re)index all documents of the given symbols.

    force=True bypasses the mtime-skip check — needed when the CLEANING CODE
    changed but source files didn't (mtimes are unchanged, so the manifest
    would otherwise skip every file)."""
    col = get_collection()
    emb = get_embedder()
    manifest = _load_manifest()
    stats = {"files_indexed": 0, "files_skipped": 0, "chunks": 0, "errors": 0}

    for si, sym in enumerate(symbols, 1):
        sym = sym.upper()
        company = sym
        try:
            from .data_access import entities
            ent = entities()
            row = ent[ent["symbol"] == sym]
            if not row.empty:
                company = str(row.iloc[0]["company_name"])
        except Exception:
            pass

        batch_texts, batch_ids, batch_metas = [], [], []
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories
        seen_ann_keys: set[str] = set()   # same letter filed under 2 categories

        def flush():
            if not batch_texts:
                return
            # DOC_PREFIX is required by nomic-embed-text-v1.5's training convention
            # for the embedding input, but must NOT be stored — Claude reads the
            # stored document text verbatim at retrieval time.
            prefixed = [DOC_PREFIX + t for t in batch_texts]
            vecs = emb.encode(prefixed, batch_size=EMBED_BATCH,
                              normalize_embeddings=True, show_progress_bar=False)
            col.upsert(ids=list(batch_ids), embeddings=vecs.tolist(),
                       documents=list(batch_texts), metadatas=list(batch_metas))
            stats["chunks"] += len(batch_texts)
            batch_texts.clear(); batch_ids.clear(); batch_metas.clear()

        for path, doc_type in iter_company_files(sym):
            rel = str(path.relative_to(ROOT))
            mtime = path.stat().st_mtime
            if not force and manifest.get(rel) == mtime:
                stats["files_skipped"] += 1
                continue
            try:
                chunks = file_chunks(path, doc_type, sym)
            except Exception as e:
                progress(f"  ! {rel}: {type(e).__name__}: {e}")
                stats["errors"] += 1
                continue
            # remove stale chunks of this file before re-adding
            if manifest.get(rel) is not None:
                try:
                    col.delete(where={"source": rel})
                except Exception:
                    pass
            for i, (text, meta) in enumerate(chunks):
                if not text.strip():
                    continue
                dk = meta.pop("dedupe_key", None)
                if dk:
                    if dk in seen_ann_keys:
                        continue
                    seen_ann_keys.add(dk)
                batch_ids.append(f"{rel}::{i}")
                batch_texts.append(text[:8000])
                batch_metas.append({
                    "symbol": sym, "company": company, "doc_type": doc_type,
                    "source": rel, "seq": i,
                    "date_int": int(meta.get("date_int") or 0),
                    "period": str(meta.get("period") or ""),
                    "section": str(meta.get("section") or ""),
                    "quality": str(meta.get("quality") or ""),
                })
                if len(batch_texts) >= 512:
                    flush()
            manifest[rel] = mtime
            stats["files_indexed"] += 1
        flush()
        _save_manifest(manifest)
        progress(f"[{si}/{len(symbols)}] {sym}: files+{stats['files_indexed']} "
                 f"skip{stats['files_skipped']} chunks={stats['chunks']}")
    return stats


# ------------------------------------------------------------------ search --
def _where(symbol=None, doc_types=None, date_from=None, date_to=None, date_eq=None):
    conds = []
    if symbol:
        conds.append({"symbol": {"$eq": symbol.upper()}})
    if doc_types:
        conds.append({"doc_type": {"$in": list(doc_types)}})
    if date_eq is not None:
        conds.append({"date_int": {"$eq": int(date_eq)}})
    else:
        if date_from:
            conds.append({"date_int": {"$gte": int(date_from)}})
        if date_to:
            conds.append({"date_int": {"$lte": int(date_to)}})
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


def _run_query(qvec, where, k) -> list[dict]:
    res = get_collection().query(query_embeddings=qvec, n_results=k, where=where,
                                 include=["documents", "metadatas", "distances"])
    out = []
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        out.append({
            "text": doc,
            "symbol": meta.get("symbol"), "company": meta.get("company"),
            "doc_type": meta.get("doc_type"), "period": meta.get("period"),
            "date_int": meta.get("date_int"), "source": meta.get("source"),
            "score": round(1 - dist, 4),
        })
    return out


def _qvec(query: str):
    return get_embedder().encode([QUERY_PREFIX + query],
                                 normalize_embeddings=True).tolist()


def search(query: str, symbol: str | None = None, doc_types: list[str] | None = None,
           date_from: int | None = None, date_to: int | None = None,
           k: int = 8, max_per_symbol: int | None = None) -> list[dict]:
    """Semantic search. max_per_symbol caps hits per company (for cross-company
    thematic queries where one company would otherwise dominate)."""
    qvec = _qvec(query)
    where = _where(symbol, doc_types, date_from, date_to)
    fetch_k = k * 5 if (max_per_symbol and not symbol) else k
    hits = _run_query(qvec, where, fetch_k)
    if max_per_symbol and not symbol:
        seen: dict[str, int] = {}
        capped = []
        for h in hits:
            s = h["symbol"]
            if seen.get(s, 0) < max_per_symbol:
                capped.append(h)
                seen[s] = seen.get(s, 0) + 1
            if len(capped) >= k:
                break
        hits = capped
    return hits


def search_timeline(query: str, symbol: str, doc_types: list[str] | None = None,
                    n_periods: int = 8, per_period: int = 1) -> dict[str, list[dict]]:
    """Best chunk(s) per period, newest first — for 'how did X evolve over quarters'.

    Runs one retrieval per distinct document date so every period is represented,
    instead of letting global top-k cluster in whichever quarter matches best.
    """
    col = get_collection()
    dt = doc_types or ["concall_transcript", "concall_presentation"]
    got = col.get(where=_where(symbol, dt), include=["metadatas"])
    dates = sorted({m["date_int"] for m in got["metadatas"] if m.get("date_int")},
                   reverse=True)[:n_periods]
    if not dates:
        return {}
    qvec = _qvec(query)
    out: dict[str, list[dict]] = {}
    for d in dates:
        hits = _run_query(qvec, _where(symbol, dt, date_eq=d), per_period)
        if hits:
            out[hits[0]["period"] or str(d)] = hits
    return out


def index_stats() -> dict:
    col = get_collection()
    n = col.count()
    manifest = _load_manifest()
    syms = {Path(k).parts[2] for k in manifest} if manifest else set()
    return {"chunks": n, "files": len(manifest), "companies_indexed": sorted(syms)}
