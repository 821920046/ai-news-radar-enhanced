#!/usr/bin/env python3
"""V3 AI News Radar — CLI entry point.
Delegates to core.pipeline.main_pipeline.Pipeline for backward compatibility.
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path

# Ensure project root is on sys.path
_project_root = str(_Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core.pipeline.main_pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())
