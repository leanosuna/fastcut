#!/usr/bin/env python3
"""
FastCut: A simple cross-platform video editor with GPU acceleration.
"""

from __future__ import annotations

import os
import sys

# Add src to path
SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from main_window import MainWindow
from styles import DARK_STYLESHEET


def main():
    # High DPI scaling attributes
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("FastCut")
    app.setApplicationDisplayName("FastCut Video Editor")
    app.setStyleSheet(DARK_STYLESHEET)

    initial_file = None
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        initial_file = os.path.abspath(sys.argv[1])

    window = MainWindow(initial_file=initial_file)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
