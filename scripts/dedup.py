"""Shim: re-exports from core.dedup.deduplicator for backward compatibility."""
from core.dedup.deduplicator import *  # noqa: F401, F403
from core.dedup.deduplicator import (  # explicitly export underscore-prefixed internals used by tests
    _pick_best_item,
    _pick_best_item as _pick_best_item,
)
