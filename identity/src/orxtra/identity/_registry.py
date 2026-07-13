from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.protocols import BUILTIN_KINDS

if TYPE_CHECKING:
    from collections.abc import Iterable


class KindRegistry:
    """Instance-scoped registry of valid principal kinds.

    Deliberately holds NO module-global mutable state: every composition
    root constructs its own registry from the app-declared kinds. The
    built-in kinds (``BUILTIN_KINDS`` from protocols) are always registered.

    The registry is only the mechanism for kind validation. The enforcement
    POINT -- where ``validate`` is actually called before a principal is
    minted -- lives in the service layer (``orxtra.services._identity``).
    """

    def __init__(self, app_kinds: Iterable[str]) -> None:
        kinds = set(BUILTIN_KINDS)
        for kind in app_kinds:
            if not kind or not kind.strip():
                msg = "App-declared principal kind must be a non-blank string"
                raise ValueError(msg)
            if kind in kinds:
                msg = (
                    f"Duplicate principal kind {kind!r}: already registered "
                    f"(built-in or previously declared)"
                )
                raise ValueError(msg)
            kinds.add(kind)
        self._kinds = frozenset(kinds)

    @property
    def kinds(self) -> frozenset[str]:
        return self._kinds

    def validate(self, kind: str) -> None:
        """Hard error if ``kind`` is not registered."""
        if kind not in self._kinds:
            registered = ", ".join(sorted(self._kinds))
            msg = (
                f"Unknown principal kind {kind!r}. Registered kinds: "
                f"{registered}"
            )
            raise ValueError(msg)
