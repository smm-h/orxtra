"""Composition engine: collects, orders, and assembles fragments."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxtra.compose._variables import resolve_variables

if TYPE_CHECKING:
    from orxtra.compose._fragment import Fragment, FragmentProvider

_DEFAULT_SEPARATOR = "\n\n---\n\n"


class CompositionEngine:
    """Collects fragments from providers, orders by priority, assembles text.

    Fragments are sorted by priority (ascending), with deterministic
    tiebreaking by name (alphabetical). After assembly, optional strict
    variable substitution is applied.
    """

    def __init__(
        self,
        providers: list[FragmentProvider],
        separator: str = _DEFAULT_SEPARATOR,
    ) -> None:
        self._providers = list(providers)
        self._separator = separator

    def compose(
        self,
        context: dict[str, Any],
        variables: dict[str, str] | None = None,
    ) -> str:
        """Compose all fragments into a single string.

        1. Collects fragments from all providers (passing context)
        2. Sorts by priority (deterministic tiebreak: by name, alphabetical)
        3. Concatenates content with section separators
        4. If variables provided: applies strict two-way variable substitution
        5. Returns the composed string
        """
        all_fragments: list[Fragment] = []
        for provider in self._providers:
            all_fragments.extend(provider.fragments(context))

        all_fragments.sort(key=lambda f: (f.priority, f.name))

        composed = self._separator.join(f.content for f in all_fragments)

        if variables is not None:
            composed = resolve_variables(composed, variables)

        return composed
