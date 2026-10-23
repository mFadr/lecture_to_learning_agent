"""Vstupní bod pro PyInstaller (build_exe.bat)."""

import sys

from lecture_to_learning.ui.app import main

if __name__ == "__main__":
    sys.exit(main())
