#!/usr/bin/env python3
"""Streamlit Cloud default entry - delegates to app_9edge_ui."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app_9edge_ui import main

main()
