from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from orxtra.protocols import SurfaceOperation

    type TemplateFn = Callable[[dict[str, object]], list[SurfaceOperation]]


class SurfaceRegistry:
    """Registry mapping template names to template functions."""

    def __init__(
        self,
        templates: dict[str, TemplateFn] | None = None,
    ) -> None:
        self._templates: dict[str, TemplateFn] = dict(templates) if templates else {}

    def register(self, name: str, template_fn: TemplateFn) -> None:
        self._templates[name] = template_fn

    def get(self, name: str) -> TemplateFn:
        """Retrieve a template function by name.

        Raises KeyError if the template is not registered.
        """
        try:
            return self._templates[name]
        except KeyError:
            msg = f"Template {name!r} not found. Available: {sorted(self._templates)}"
            raise KeyError(msg) from None

    def list_templates(self) -> list[str]:
        return sorted(self._templates)
