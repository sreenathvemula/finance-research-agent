#!/usr/bin/env python3
"""One-shot re-parse of transcript JSONs broken by the pre-fix parser.

Targets:
  (a) content-lost files: json_size < 0.15 * sibling md_size
  (b) all BHARTIARTL transcript files (structurally broken 'Final Transcript' header)

Re-parses directly from the existing clean markdown at
data/companies/<SYM>/concalls/*.md (source PDFs are no longer on disk),
using the FIXED scripts/22_transcript_parser.py, and overwrites the JSON
in place at data/companies/<SYM>/concalls/*.json (the live location the
RAG pipeline reads).

Usage:
  python scripts/_reparse_broken_transcripts.py --list     # show targets, no writes
  python scripts/_reparse_broken_transcripts.py --run
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
COMPANIES = ROOT / "data" / "companies"


def _imp(name, fname):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def find_targets(threshold: float = 0.15) -> list[tuple[Path, Path, str]]:
    """Return [(md_path, json_path, symbol)] needing re-parse."""
    targets = []
    for sym_dir in sorted(COMPANIES.iterdir()):
        if not sym_dir.is_dir():
            continue
        sym = sym_dir.name
        cc = sym_dir / "concalls"
        if not cc.is_dir():
            continue
        for jp in sorted(cc.glob("*_transcript.json")):
            mp = jp.with_suffix(".md")
            if not mp.exists():
                continue
            md_size = mp.stat().st_size
            json_size = jp.stat().st_size
            content_lost = md_size > 0 and json_size < threshold * md_size
            is_bhartiartl = sym == "BHARTIARTL"
            if content_lost or is_bhartiartl:
                targets.append((mp, jp, sym))
    return targets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()

    targets = find_targets()
    print(f"targets: {len(targets)}")
    by_sym = {}
    for mp, jp, sym in targets:
        by_sym.setdefault(sym, []).append(mp.name)
    for sym, files in sorted(by_sym.items()):
        print(f"  {sym}: {len(files)} file(s)")

    if args.list or not args.run:
        return

    tp = _imp("tp", "22_transcript_parser.py")
    ok, fail = 0, 0
    for mp, jp, sym in targets:
        try:
            md_text = mp.read_text(encoding="utf-8", errors="ignore")
            period = tp.parse_period(mp.stem)
            parsed = tp.parse_transcript(md_text, sym, period)
            q = tp.assess_quality(parsed, md_text)
            parsed["parse_quality"] = q["quality"]
            parsed["quality_reasons"] = q["reasons"]
            jp.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
            n_ex = len(parsed.get("exchanges") or [])
            n_pr = len(parsed.get("prepared_remarks") or [])
            print(f"  ok  {sym}/{mp.name}: quality={q['quality']} "
                  f"exchanges={n_ex} prepared_remarks={n_pr} "
                  f"({jp.stat().st_size:,}B, was tiny/broken)")
            ok += 1
        except Exception as e:
            print(f" FAIL {sym}/{mp.name}: {type(e).__name__}: {e}")
            fail += 1
    print(f"\nre-parsed: {ok} ok, {fail} failed")


if __name__ == "__main__":
    main()
