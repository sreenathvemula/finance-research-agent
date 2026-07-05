"""Build/refresh the RAG index.

Usage (from the Finance folder, in the finance-ai env):
  python -m agent.build_index --symbols RELIANCE,TCS,INFY
  python -m agent.build_index --top 100          # top-N companies by market cap
  python -m agent.build_index --all              # full corpus (long run, resumable)
  python -m agent.build_index --stats
"""
import argparse
import time

from .config import COMPANIES


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="comma-separated symbols")
    ap.add_argument("--top", type=int, help="index top-N companies by market cap")
    ap.add_argument("--all", action="store_true", help="index every company dir")
    ap.add_argument("--stats", action="store_true", help="show index stats and exit")
    ap.add_argument("--force", action="store_true",
                    help="bypass mtime-skip (needed after a cleaning-code change "
                         "with unchanged source files)")
    args = ap.parse_args()

    from . import rag

    if args.stats:
        s = rag.index_stats()
        print(f"chunks: {s['chunks']}, files: {s['files']}, "
              f"companies: {len(s['companies_indexed'])}")
        print(", ".join(s["companies_indexed"][:50]))
        return

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.top:
        from .data_access import entities
        ent = entities().sort_values("market_cap_cr", ascending=False)
        symbols = ent["symbol"].head(args.top).tolist()
    elif args.all:
        symbols = sorted(p.name for p in COMPANIES.iterdir() if p.is_dir())
    else:
        ap.error("one of --symbols / --top / --all / --stats required")
        return

    print(f"indexing {len(symbols)} companies {'(forced re-embed)' if args.force else ''} ...")
    t0 = time.time()
    stats = rag.index_symbols(symbols, force=args.force)
    dt = time.time() - t0
    print(f"done in {dt/60:.1f} min: {stats}")


if __name__ == "__main__":
    main()
