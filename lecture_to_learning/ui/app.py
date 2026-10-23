"""Spuštění desktopové aplikace."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from .. import APP_DISPLAY_NAME
from ..config import data_dir, load_settings
from ..logging_setup import setup_logging
from ..storage import ProjectStore


def main() -> int:
    settings = load_settings()
    root = data_dir(settings)
    log_file = setup_logging(root / "logs")
    log = logging.getLogger("lecture_to_learning")
    log.info("Start aplikace, data: %s", root)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setStyle("Fusion")

    def excepthook(exc_type, exc, tb):  # neošetřené výjimky v UI vlákně – zalogovat a ukázat
        log.critical("Neošetřená chyba", exc_info=(exc_type, exc, tb))
        QMessageBox.critical(None, "Neočekávaná chyba", f"{exc}\n\nPodrobnosti: {log_file}")
    sys.excepthook = excepthook

    try:
        store = ProjectStore(root)
    except Exception as exc:
        QMessageBox.critical(None, APP_DISPLAY_NAME, f"Nelze otevřít datovou složku {root}: {exc}")
        return 1
    from .main_window import MainWindow
    win = MainWindow(store, settings)
    win.show()
    return app.exec()
