"""Matplotlib chart rendering for tool outputs.

Charts are generated INSIDE the tool (not by the calling agent) so they work in
any harness — Claude Desktop, a terminal SDK session, anything — not just one
that happens to have a separate visualization tool available. Returns PNG bytes;
the tool wrapper base64-encodes them into an MCP image content block.
"""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")  # headless — no display server needed
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# A clean, non-default look: soft gridlines, no chart junk, readable at chat width.
plt.rcParams.update({
    "figure.dpi": 160,
    "savefig.dpi": 160,
    "font.size": 11,
    "font.family": "sans-serif",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": "#888",
    "axes.grid": True,
    "grid.color": "#e3e3e3",
    "grid.linewidth": 0.8,
    "axes.axisbelow": True,
})

_BLUE, _ORANGE, _PURPLE, _RED = "#2a78d6", "#eb6834", "#4a3aa7", "#c94040"


def _fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _year_labels(period_strs: list[str]) -> list[str]:
    # "2019-03-01" -> "FY19"; falls back to the raw string if unparsable
    out = []
    for p in period_strs:
        try:
            out.append("FY" + p[2:4])
        except Exception:
            out.append(p)
    return out


def capital_allocation_chart(symbol: str, cfo: list, capex: list, fcf: list,
                             net_debt: list | None = None) -> bytes:
    """Per-year 2D line plot: CFO, capex, FCF (shaded below zero), each a
    distinct line+marker — no bars, no dual axis, one clear trend per year."""
    years = _year_labels([p for p, _ in cfo])
    cfo_v = [v for _, v in cfo]
    capex_v = [v for _, v in capex]
    fcf_v = [v for _, v in fcf]

    fig, ax = plt.subplots(figsize=(7.5, 4.3))
    ax.plot(years, cfo_v, marker="o", ms=5, lw=2.2, color=_BLUE, label="Cash from operations")
    ax.plot(years, capex_v, marker="o", ms=5, lw=2.2, color=_ORANGE, label="Capex")
    ax.plot(years, fcf_v, marker="o", ms=5, lw=2.2, color=_PURPLE, label="Free cash flow")
    ax.axhline(0, color="#888", lw=1)
    ax.fill_between(years, fcf_v, 0, where=[v < 0 for v in fcf_v],
                    color=_RED, alpha=0.15, interpolate=True, zorder=0)

    for x, v in zip(years, fcf_v):
        if v < 0:
            ax.annotate(f"-{abs(v):,.0f}", (x, v), textcoords="offset points",
                       xytext=(0, -14), ha="center", fontsize=9, color=_RED)

    fig.suptitle(f"{symbol.upper()} — cash flow vs capex, per fiscal year (Rs crore)",
                fontsize=12, x=0.02, y=0.99, ha="left")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_ylabel("Rs crore")
    ax.legend(frameon=False, loc="upper center", ncol=3, fontsize=9,
              bbox_to_anchor=(0.5, 1.06))
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    return _fig_to_png(fig)


def price_trend_chart(symbol: str, dates: list, closes: list,
                      dma50: list | None = None, dma200: list | None = None) -> bytes:
    """Simple 2D price line with optional moving averages."""
    fig, ax = plt.subplots(figsize=(7.5, 4.0))
    ax.plot(dates, closes, lw=1.6, color=_BLUE, label="Close")
    if dma50:
        ax.plot(dates, dma50, lw=1.2, color=_ORANGE, label="50-DMA")
    if dma200:
        ax.plot(dates, dma200, lw=1.2, color=_PURPLE, label="200-DMA")
    ax.set_title(f"{symbol.upper()} — price trend", fontsize=12, pad=10, loc="left")
    ax.set_ylabel("Price (Rs)")
    ax.legend(frameon=False, loc="upper left", fontsize=9)
    fig.autofmt_xdate()
    fig.tight_layout()
    return _fig_to_png(fig)
