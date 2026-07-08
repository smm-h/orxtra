from __future__ import annotations

from typing import Any

from orxtra.protocols import (
    CreateSurface,
    SurfaceOperation,
    UpdateComponents,
    UpdateDataModel,
)


def _resolve_pointer(data: dict[str, Any], pointer: str) -> object:
    """Resolve a JSON Pointer path against a data dict.

    Paths start with ``/`` and use ``/`` as separator.
    Returns None if the path cannot be resolved.
    """
    parts = pointer.strip("/").split("/")
    current: object = data
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _resolve_value(value: object, data: dict[str, Any]) -> object:
    """Resolve a value, replacing JSON Pointer references.

    Strings starting with ``$`` followed by ``/path`` are resolved
    against the data dict.  All other values pass through unchanged.
    """
    if isinstance(value, str) and value.startswith("$") and "/" in value:
        pointer = value[1:]  # strip leading $
        return _resolve_pointer(data, pointer)
    return value


def _resolve_properties(
    props: dict[str, Any],
    data: dict[str, Any],
) -> dict[str, Any]:
    """Resolve all JSON Pointer references in a property dict."""
    resolved: dict[str, Any] = {}
    for key, val in props.items():
        if isinstance(val, dict):
            resolved[key] = _resolve_properties(val, data)
        elif isinstance(val, list):
            resolved[key] = [
                _resolve_properties(item, data) if isinstance(item, dict)
                else _resolve_value(item, data)
                for item in val
            ]
        else:
            resolved[key] = _resolve_value(val, data)
    return resolved


class TemplateEngine:
    """Resolves data bindings in component definitions to produce SurfaceOperations."""

    def populate(
        self,
        components: list[dict[str, Any]],
        data: dict[str, Any],
        *,
        surface_id: str = "default",
        catalog_id: str = "default",
    ) -> list[SurfaceOperation]:
        """Take component definitions and a data dict, resolve bindings.

        Returns a list of SurfaceOperations: a CreateSurface, an
        UpdateComponents with resolved component properties, and an
        UpdateDataModel with the full data dict.
        """
        resolved_components: list[dict[str, Any]] = []
        for comp in components:
            resolved = dict(comp)
            if "properties" in resolved:
                resolved["properties"] = _resolve_properties(
                    resolved["properties"], data,
                )
            resolved_components.append(resolved)

        return [
            CreateSurface(surface_id=surface_id, catalog_id=catalog_id),
            UpdateComponents(surface_id=surface_id, components=resolved_components),
            UpdateDataModel(surface_id=surface_id, path="/", value=data),
        ]
