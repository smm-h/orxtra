from __future__ import annotations

from types import ModuleType
from typing import cast


def require_cognee() -> ModuleType:
    """Import and return the cognee module, raising RuntimeError if not installed."""
    try:
        import cognee  # noqa: PLC0415
    except ImportError:
        msg = (
            "cognee is required for the knowledge module."
            " Install it with: uv add cognee"
        )
        raise RuntimeError(msg) from None
    else:
        return cast(ModuleType, cognee)
