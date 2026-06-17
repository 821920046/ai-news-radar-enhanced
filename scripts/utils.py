"""Shim: re-exports from core.utils for backward compatibility."""
from core.utils import *  # noqa: F401, F403
from core.utils import _env_int  # noqa: F401 (private but used by ai_processor)
