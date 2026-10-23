"""Vykreslení vizualizace do souborů projektu (SVG/PNG/Mermaid) a převod SVG→PNG."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from ..models import Visualization, VizType
from ..security import safe_filename
from .charts import render_chart
from .mermaid import to_mermaid
from .svg import render_svg
from .validate import CHART_TYPES

log = logging.getLogger(__name__)


class RenderError(Exception):
    pass


def viz_basename(viz: Visualization) -> str:
    return safe_filename(f"{viz.spec.title[:50]}_{viz.id}_v{viz.version}", max_len=90, has_ext=False)


def render_to_files(viz: Visualization, project_dir: Path, footer: str) -> Visualization:
    """Vykreslí vizualizaci do složky visualizations/. Cesty uloží relativně k projektu."""
    out_dir = project_dir / "visualizations"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = viz_basename(viz)
    svg_path = out_dir / f"{base}.svg"
    spec = viz.spec
    try:
        if spec.viz_type in CHART_TYPES or spec.viz_type == VizType.FUNCTION:
            png_path = out_dir / f"{base}.png"
            render_chart(spec, svg_path, png_path, footer)
            viz.png_path = str(png_path.relative_to(project_dir))
        else:
            svg_path.write_text(render_svg(spec, footer), encoding="utf-8")
            viz.mermaid = to_mermaid(spec)
            png_path = out_dir / f"{base}.png"
            if svg_to_png(svg_path.read_text(encoding="utf-8"), png_path):
                viz.png_path = str(png_path.relative_to(project_dir))
    except Exception as exc:
        log.exception("Vykreslení vizualizace selhalo")
        raise RenderError(f"Vykreslení selhalo: {exc}") from exc
    viz.svg_path = str(svg_path.relative_to(project_dir))
    return viz


def svg_to_png(svg_text: str, out: Path, scale: float = 2.0) -> bool:
    """Převede SVG na PNG pomocí Qt (QtSvg). Vrací False, pokud Qt není k dispozici."""
    try:
        from PySide6.QtCore import QByteArray, QCoreApplication, Qt
        from PySide6.QtGui import QGuiApplication, QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        return False
    if QCoreApplication.instance() is None:
        # mimo GUI (testy, CLI): vykreslování textu vyžaduje QGuiApplication – vytvoříme offscreen
        if threading.current_thread() is not threading.main_thread():
            return False
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        global _app_ref
        _app_ref = QGuiApplication([])
    _ensure_fonts()
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    if not renderer.isValid():
        return False
    size = renderer.defaultSize()
    img = QImage(int(size.width() * scale), int(size.height() * scale), QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.white)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()
    return img.save(str(out), "PNG")


_app_ref = None
_fonts_loaded = False


def _ensure_fonts() -> None:
    """Offscreen platforma Qt nemá na Windows databázi písem – zaregistrujeme systémová písma."""
    global _fonts_loaded
    if _fonts_loaded:
        return
    _fonts_loaded = True
    from PySide6.QtGui import QFontDatabase, QGuiApplication
    if QGuiApplication.platformName() != "offscreen":
        return
    windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("segoeui.ttf", "segoeuib.ttf", "arial.ttf", "arialbd.ttf"):
        f = windir / name
        if f.exists():
            QFontDatabase.addApplicationFont(str(f))
