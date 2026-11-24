#!/usr/bin/env python3 -I
"""Isolated-mode launcher: keeps -I semantics while reaching user site libs."""
import sys
from pathlib import Path

sys.path.insert(0, "/home/node/.local/lib/python3.11/site-packages")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from workbench.cli import main

if __name__ == "__main__":
    main()
