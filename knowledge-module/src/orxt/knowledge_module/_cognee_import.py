from __future__ import annotations

from types import ModuleType


def require_cognee() -> ModuleType:
    """Import and return the cognee module, raising RuntimeError if not installed."""
    try:
        import cognee  # type: ignore[import-untyped]  # noqa: PLC0415

        return cognee
    except ImportError:
        msg = "cognee is required for the knowledge module. Install it with: uv add cognee"
        raise RuntimeError(msg) from None
