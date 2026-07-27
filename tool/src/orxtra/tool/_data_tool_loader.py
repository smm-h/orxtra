"""Loader for data-defined tool TOML definitions.

Parses ``.toml`` files, validates with the pydantic schema, enforces
the ``custom.`` namespace root, and validates ``{{secret:NAME}}``
references against the secret registry.
"""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, Any

from orxtra.tool._data_tool_types import DataToolDefinition
from orxtra.tool._gen_data_tool import validate_bytes as _validate_data_tool_document
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from orxtra.secrets import SecretRegistry


class DataToolValidationError(ValueError):
    """A data-tool document failed strictspec validation at the load boundary."""


def load_tool_definition(
    path: Path,
    secret_registry: SecretRegistry | None = None,
) -> DataToolDefinition:
    """Parse and validate a single data-defined tool TOML file.

    Args:
        path: Path to the ``.toml`` file.
        secret_registry: When provided, all ``{{secret:NAME}}``
            references in the file are validated against the registry.

    Returns:
        A validated ``DataToolDefinition``.

    Raises:
        FileNotFoundError: If the path does not exist.
        ValueError: On validation failure (wraps pydantic errors).
        KeyError: On unknown secret references.
    """
    if not path.is_file():
        msg = f"Tool definition file not found: {path}"
        raise FileNotFoundError(msg)

    text = path.read_text()
    # strictspec document gate: enforces integer format_version, the [tool]
    # identity shape (incl. the custom.* namespace pattern), optional [params],
    # the discriminated [execution] union (http/monty/command), and the optional
    # [output] block, plus unknown-key rejection in every section. Subsumes the
    # hand-rolled missing-[tool], missing-[execution], and unknown-top-level-
    # section checks. Secret-reference validation stays consumer-native below.
    _root, diags = _validate_data_tool_document(text.encode("utf-8"), "toml")
    if diags:
        detail = "\n".join(f"  {d.code} at {d.path}: {d.message}" for d in diags)
        msg = f"Invalid data-tool document ({path}):\n{detail}"
        raise DataToolValidationError(msg)
    raw = tomllib.loads(text)

    # The TOML structure uses section headers that map to nested dicts:
    #   [tool] -> name, description, namespace, deferred, tags
    #   [params] -> { param_name: { type, description, ... } }
    #   [execution] -> { type, ... }
    #   [output] -> { schema: ... }
    # Flatten into the model's expected shape (shape already gate-validated).
    model_input: dict[str, Any] = dict(raw["tool"])
    model_input["params"] = raw.get("params", {})
    model_input["execution"] = raw["execution"]
    if "output" in raw:
        output_section = dict(raw["output"])
        # Map the TOML key "schema" to the pydantic field "schema_".
        if "schema" in output_section:
            output_section["schema_"] = output_section.pop("schema")
        model_input["output"] = output_section

    try:
        definition = DataToolDefinition(**model_input)
    except ValidationError as exc:
        msg = f"Invalid tool definition in {path}: {exc}"
        raise ValueError(msg) from exc

    # Validate secret references if a registry is provided.
    if secret_registry is not None:
        _validate_secrets(definition, path, secret_registry)

    return definition


def _validate_secrets(
    definition: DataToolDefinition,
    path: Path,
    secret_registry: SecretRegistry,
) -> None:
    """Scan all string fields in the definition for {{secret:NAME}}
    references and validate them against the registry.

    Raises KeyError with a descriptive message on unknown references.
    """
    # Collect all text that might contain secret references.
    texts: list[str] = []

    # Tool-level fields
    texts.append(definition.description)

    # Execution-level fields
    exec_cfg = definition.execution
    from orxtra.tool._data_tool_types import (
        CommandExecution,
        HttpExecution,
        MontyExecution,
    )

    if isinstance(exec_cfg, HttpExecution):
        texts.append(exec_cfg.url)
        if exec_cfg.body_template is not None:
            texts.append(exec_cfg.body_template)
        if exec_cfg.headers is not None:
            texts.extend(exec_cfg.headers.values())
    elif isinstance(exec_cfg, MontyExecution):
        texts.append(exec_cfg.code)
    elif isinstance(exec_cfg, CommandExecution):
        texts.append(exec_cfg.executable)

    combined = "\n".join(texts)
    try:
        secret_registry.validate_references(combined)
    except KeyError as exc:
        msg = f"Secret validation failed in {path}: {exc}"
        raise KeyError(msg) from exc


def load_tool_definitions(
    directory: Path,
    secret_registry: SecretRegistry | None = None,
) -> list[DataToolDefinition]:
    """Load all data-defined tool TOML files from a directory.

    Args:
        directory: Path to the directory containing ``.toml`` files.
        secret_registry: When provided, all secret references are
            validated against the registry.

    Returns:
        A list of validated ``DataToolDefinition`` objects.

    Raises:
        FileNotFoundError: If the directory does not exist.
        ValueError: On validation failure or name collision.
        KeyError: On unknown secret references.
    """
    if not directory.is_dir():
        msg = f"Tools directory not found: {directory}"
        raise FileNotFoundError(msg)

    definitions: list[DataToolDefinition] = []
    seen_names: dict[str, Path] = {}

    for toml_path in sorted(directory.glob("*.toml")):
        definition = load_tool_definition(toml_path, secret_registry)
        if definition.name in seen_names:
            msg = (
                f"Duplicate data-defined tool name {definition.name!r}: "
                f"defined in both {seen_names[definition.name]} and {toml_path}"
            )
            raise ValueError(msg)
        seen_names[definition.name] = toml_path
        definitions.append(definition)

    return definitions
