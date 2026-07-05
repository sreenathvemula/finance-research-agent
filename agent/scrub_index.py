"""Surgically remove leaked cover-letter/artifact chunks from the live index.

No re-embedding — just deletes chunk ids that match strong leak criteria:
  - presentations/transcripts: >=2 distinct letterhead markers, or picture placeholders
  - annual reports: >=3 distinct letterhead markers (statutory letters inside ARs
    legitimately contain 1-2)
Announcements are never touched (single subject+body chunks).

Usage:  python -m agent.scrub_index [--dry-run]
Safe to re-run any time (idempotent); run again after a full index build.
"""
import re
import sys
from collections import Counter

from .clean import count_letterhead_markers


def main():
    dry = "--dry-run" in sys.argv
    from . import rag
    col = rag.get_collection()
    total = col.count()
    print(f"chunks: {total:,}")

    pic = re.compile(r"intentionally omitted")
    to_delete: list[str] = []
    reasons = Counter()
    offset, batch = 0, 5000
    while offset < total:
        r = col.get(limit=batch, offset=offset, include=["documents", "metadatas"])
        if not r["ids"]:
            break
        for cid, doc, m in zip(r["ids"], r["documents"], r["metadatas"]):
            dt = m.get("doc_type", "")
            if dt == "announcement":
                continue
            if pic.search(doc):
                to_delete.append(cid)
                reasons["picture placeholder"] += 1
                continue
            nmark = count_letterhead_markers(doc)
            threshold = 3 if dt == "annual_report" else 2
            if nmark >= threshold:
                to_delete.append(cid)
                reasons[f"{dt}: >={threshold} letterhead markers"] += 1
        offset += len(r["ids"])

    print(f"leak chunks found: {len(to_delete):,} ({100*len(to_delete)/max(1,total):.3f}%)")
    for k, v in reasons.most_common():
        print(f"  {k}: {v:,}")
    if dry or not to_delete:
        print("dry run — nothing deleted" if dry else "nothing to delete")
        return
    for i in range(0, len(to_delete), 1000):
        col.delete(ids=to_delete[i:i + 1000])
    print(f"deleted {len(to_delete):,} chunks; index now {col.count():,}")


if __name__ == "__main__":
    main()
