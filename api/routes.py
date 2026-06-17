"""API routes — re-exported for modular routing (optional).

For now all routes are in api/app.py. This file provides a hook
for splitting routes into sub-modules later.
"""

from __future__ import annotations

from api.app import app

__all__ = ["app"]
