"""One-shot: force re-embed the companies affected by the clean.py fixes
(reads the symbol list produced by the transcript re-parse verification)."""
import time

path = (r"C:\Users\GE66\AppData\Local\Temp\claude\C--Users-GE66-Downloads-Finance"
        r"\bad1ba4a-4dd4-4bf4-90b4-edeebf5ddaee\scratchpad\reparsed_syms.txt")
symbols = open(path, encoding="utf-8").read().strip().split(",")
print(f"force re-embedding {len(symbols)} companies")

from agent import rag
t0 = time.time()
stats = rag.index_symbols(symbols, force=True)
print(f"done in {(time.time()-t0)/60:.1f} min: {stats}")
