"""Grafy (Matplotlib) z validovaných dat – výstup SVG a PNG."""

from __future__ import annotations

import io
import threading
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from ..models import VizSpec, VizType  # noqa: E402
from .safe_math import sample  # noqa: E402
from .svg import STROKES  # noqa: E402

_lock = threading.Lock()  # pyplot není thread-safe; používáme Figure API, ale pro jistotu zámek


def _figure(spec: VizSpec, footer: str) -> Figure:
    fig = Figure(figsize=(8, 5), dpi=110)
    ax = fig.add_subplot(111)
    t = spec.viz_type
    if t == VizType.FUNCTION:
        lo = spec.x_min if spec.x_min is not None else -10.0
        hi = spec.x_max if spec.x_max is not None else 10.0
        xs, ys = sample(spec.expression, lo, hi)
        ax.plot(xs, [float("nan") if y is None else y for y in ys], color=STROKES[0], linewidth=2,
                label=f"f(x) = {spec.expression}")
        ax.axhline(0, color="#9aa5b1", linewidth=0.8)
        ax.axvline(0, color="#9aa5b1", linewidth=0.8)
        ax.legend(loc="best")
    elif t == VizType.HISTOGRAM:
        for i, s in enumerate(spec.series):
            ax.hist(s.y, bins="auto", color=STROKES[i % len(STROKES)], alpha=0.75, edgecolor="white",
                    label=s.name or None)
        if len(spec.series) > 1:
            ax.legend()
    elif t == VizType.BAR:
        n = len(spec.series)
        cats = [str(x) for x in spec.series[0].x]
        width = 0.8 / max(1, n)
        for i, s in enumerate(spec.series):
            pos = [j + (i - (n - 1) / 2) * width for j in range(len(s.y))]
            bars = ax.bar(pos, s.y, width=width, color=STROKES[i % len(STROKES)], label=s.name or None)
            ax.bar_label(bars, fmt="%g", fontsize=8, padding=2)
        ax.set_xticks(range(len(cats)))
        ax.set_xticklabels(cats, rotation=20 if max((len(c) for c in cats), default=0) > 8 else 0, ha="right"
                           if max((len(c) for c in cats), default=0) > 8 else "center")
        if n > 1 or spec.series[0].name:
            ax.legend()
    else:  # line, scatter
        for i, s in enumerate(spec.series):
            xs = list(s.x)
            categorical = any(isinstance(v, str) for v in xs)
            xv = list(range(len(xs))) if categorical else [float(v) for v in xs]
            c = STROKES[i % len(STROKES)]
            if t == VizType.SCATTER:
                ax.scatter(xv, s.y, color=c, label=s.name or None, s=36)
            else:
                ax.plot(xv, s.y, marker="o", color=c, linewidth=2, label=s.name or None)
            if categorical:
                ax.set_xticks(xv)
                ax.set_xticklabels([str(v) for v in xs])
        if any(s.name for s in spec.series):
            ax.legend()
    ax.set_title(spec.title, fontsize=13, fontweight="bold", loc="left")
    ax.set_xlabel(spec.x_label)
    ax.set_ylabel(spec.y_label)
    ax.grid(True, alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    if spec.illustrative_values:
        ax.text(0.99, 0.98, "MODELOVÉ HODNOTY – nejsou ze zdroje", transform=ax.transAxes, ha="right",
                va="top", fontsize=9, color="#b91c1c", fontweight="bold")
    if footer:
        fig.text(0.01, 0.01, footer, fontsize=8, color="#52606d")
    fig.tight_layout(rect=(0, 0.04 if footer else 0, 1, 1))
    return fig


def render_chart(spec: VizSpec, svg_out: Path, png_out: Path, footer: str = "") -> None:
    with _lock:
        fig = _figure(spec, footer)
        buf = io.StringIO()
        fig.savefig(buf, format="svg")
        svg_out.write_text(buf.getvalue(), encoding="utf-8")
        fig.savefig(png_out, format="png", dpi=150)
        plt.close("all")
