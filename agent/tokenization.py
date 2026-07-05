"""Real-tokenizer chunk-length measurement.

Chunk-sizing decisions must be made in the same units the embedding model
actually enforces. The v2 chunker used a len(text)//4 approximation, which
measured close to real BERT-tokenizer counts at the median but diverged badly
at the tail (p95 ratio 1.44x) — exactly where it mattered, causing ~51% of
chunks to silently exceed bge-small's 512-token hard limit at embed time.

This loads ONLY the tokenizer (not the full embedding model) — cheap, CPU-only,
no GPU/VRAM contention with the embedder, safe to call from every chunking
function without a performance penalty.
"""
from __future__ import annotations

from .config import EMBED_MODEL

_tokenizer = None


def get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        from transformers import AutoTokenizer
        _tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL)
    return _tokenizer


def tok_len(text: str) -> int:
    """Real, untruncated token count for chunk-sizing decisions (no special
    tokens — those add a small constant overhead the embedder pads for
    automatically). truncation=False is explicit: verified the library
    default already doesn't truncate here, but this must never silently
    depend on that — an undercount would defeat the whole point of using
    the real tokenizer instead of the old chars/4 approximation."""
    if not text:
        return 0
    return len(get_tokenizer()(text, add_special_tokens=False,
                               truncation=False)["input_ids"])
