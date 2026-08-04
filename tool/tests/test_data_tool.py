"""Tests for data-defined tool schema and loader.

Covers: valid TOML loading, namespace enforcement, secret validation,
unknown key rejection (extra=forbid), missing required fields, name
collision detection, and parameter pattern validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from orxtra.secrets import SecretRegistry
from orxtra.tool._data_tool_loader import load_tool_definition, load_tool_definitions
from orxtra.tool._data_tool_types import (
    CommandExecution,
    HttpExecution,
    MontyExecution,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_HTTP_TOML = """\
format_version = 1

[tool]
name = "fetch_weather"
description = "Fetch weather data from an API"
namespace = "custom.weather"
deferred = false
tags = ["readonly"]

[params.city]
type = "string"
description = "City name to look up"
required = true
pattern = "^[A-Za-z ]+$"

[params.units]
type = "string"
description = "Temperature units"
required = false

[execution]
type = "http"
method = "GET"
url = "https://api.weather.example.com/v1/{city}"

[output]
schema = { type = "object", properties = { temp = { type = "number" } } }
"""

_VALID_MONTY_TOML = """\
format_version = 1

[tool]
name = "transform_data"
description = "Transform data using a monty script"
namespace = "custom.transform"
deferred = true
tags = ["mutation"]

[params.input_path]
type = "string"
description = "Path to the input file"
required = true

[execution]
type = "monty"
code = "result = process(input_path)"
capabilities = ["file"]

[execution.limits]
max_duration_secs = 30
max_allocations = 1000
"""

_VALID_COMMAND_TOML = """\
format_version = 1

[tool]
name = "run_linter"
description = "Run a linter on the codebase"
namespace = "custom.lint"
deferred = false

[params.target]
type = "string"
description = "Target directory"
required = true

[execution]
type = "command"
executable = "ruff"
arg_validation = true
timeout_ceiling = 120
"""

_VALID_HTTP_WITH_SECRETS_TOML = """\
format_version = 1

[tool]
name = "secure_api"
description = "Call a secured API"
namespace = "custom.secure"
deferred = false

[execution]
type = "http"
method = "POST"
url = "https://api.example.com/v1/data"
body_template = '{"token": "{{secret:API_TOKEN}}"}'

[execution.headers]
Authorization = "Bearer {{secret:API_TOKEN}}"
"""


@pytest.fixture
def tmp_tool_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for tool definitions."""
    tools = tmp_path / "tools"
    tools.mkdir()
    return tools


# ---------------------------------------------------------------------------
# Valid loading tests
# ---------------------------------------------------------------------------


class TestValidLoading:
    """Test that valid TOML definitions load correctly."""

    def test_load_http_tool(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "weather.toml"
        toml_file.write_text(_VALID_HTTP_TOML)

        defn = load_tool_definition(toml_file)

        assert defn.name == "fetch_weather"
        assert defn.description == "Fetch weather data from an API"
        assert defn.namespace == "custom.weather"
        assert defn.deferred is False
        assert defn.tags == ["readonly"]
        assert "city" in defn.params
        assert defn.params["city"].type == "string"
        assert defn.params["city"].required is True
        assert defn.params["city"].pattern == "^[A-Za-z ]+$"
        assert "units" in defn.params
        assert defn.params["units"].required is False
        assert isinstance(defn.execution, HttpExecution)
        assert defn.execution.method == "GET"
        assert defn.execution.url == "https://api.weather.example.com/v1/{city}"
        assert defn.output is not None
        assert defn.output.schema_["type"] == "object"

    def test_load_monty_tool(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "transform.toml"
        toml_file.write_text(_VALID_MONTY_TOML)

        defn = load_tool_definition(toml_file)

        assert defn.name == "transform_data"
        assert defn.namespace == "custom.transform"
        assert defn.deferred is True
        assert isinstance(defn.execution, MontyExecution)
        assert defn.execution.code == "result = process(input_path)"
        assert defn.execution.capabilities == ["file"]
        assert defn.execution.limits.max_duration_secs == 30
        assert defn.execution.limits.max_allocations == 1000
        assert defn.execution.limits.max_memory is None

    def test_load_command_tool(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "linter.toml"
        toml_file.write_text(_VALID_COMMAND_TOML)

        defn = load_tool_definition(toml_file)

        assert defn.name == "run_linter"
        assert defn.namespace == "custom.lint"
        assert isinstance(defn.execution, CommandExecution)
        assert defn.execution.executable == "ruff"
        assert defn.execution.arg_validation is True
        assert defn.execution.timeout_ceiling == 120
        assert defn.output is None

    def test_load_tool_no_params(self, tmp_path: Path) -> None:
        """A tool with no [params] section is valid (empty params dict)."""
        toml_file = tmp_path / "simple.toml"
        toml_file.write_text(_VALID_COMMAND_TOML)

        defn = load_tool_definition(toml_file)

        assert defn.params == {"target": defn.params["target"]}

    def test_load_definitions_from_directory(
        self, tmp_tool_dir: Path,
    ) -> None:
        (tmp_tool_dir / "weather.toml").write_text(_VALID_HTTP_TOML)
        (tmp_tool_dir / "linter.toml").write_text(_VALID_COMMAND_TOML)

        defs = load_tool_definitions(tmp_tool_dir)

        assert len(defs) == 2
        names = {d.name for d in defs}
        assert names == {"fetch_weather", "run_linter"}

    def test_load_tool_with_no_tags(self, tmp_path: Path) -> None:
        """Tags are optional -- None when not specified."""
        toml_file = tmp_path / "linter.toml"
        toml_file.write_text(_VALID_COMMAND_TOML)

        defn = load_tool_definition(toml_file)

        assert defn.tags is None

    def test_load_http_with_headers_and_body(self, tmp_path: Path) -> None:
        toml_content = """\
format_version = 1

[tool]
name = "post_data"
description = "Post data to an endpoint"
namespace = "custom.api"
deferred = false

[execution]
type = "http"
method = "POST"
url = "https://api.example.com/v1/data"
body_template = '{"key": "value"}'

[execution.headers]
Content-Type = "application/json"
X-Custom = "header-value"
"""
        toml_file = tmp_path / "post.toml"
        toml_file.write_text(toml_content)

        defn = load_tool_definition(toml_file)

        assert isinstance(defn.execution, HttpExecution)
        assert defn.execution.headers == {
            "Content-Type": "application/json",
            "X-Custom": "header-value",
        }
        assert defn.execution.body_template == '{"key": "value"}'


# ---------------------------------------------------------------------------
# Namespace enforcement
# ---------------------------------------------------------------------------


class TestNamespaceEnforcement:
    """The custom. namespace root is enforced at load."""

    def test_wrong_namespace_root_hard_error(self, tmp_path: Path) -> None:
        bad_toml = _VALID_HTTP_TOML.replace(
            'namespace = "custom.weather"',
            'namespace = "ext.weather"',
        )
        toml_file = tmp_path / "bad_ns.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(
            ValueError,
            match="namespace",
        ):
            load_tool_definition(toml_file)

    def test_builtin_namespace_rejected(self, tmp_path: Path) -> None:
        bad_toml = _VALID_HTTP_TOML.replace(
            'namespace = "custom.weather"',
            'namespace = "fs.read"',
        )
        toml_file = tmp_path / "bad_ns.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="namespace"):
            load_tool_definition(toml_file)

    def test_empty_namespace_rejected(self, tmp_path: Path) -> None:
        bad_toml = _VALID_HTTP_TOML.replace(
            'namespace = "custom.weather"',
            'namespace = ""',
        )
        toml_file = tmp_path / "bad_ns.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="namespace"):
            load_tool_definition(toml_file)


# ---------------------------------------------------------------------------
# Secret validation
# ---------------------------------------------------------------------------


class TestSecretValidation:
    """Load-time hard error on unknown {{secret:NAME}} references."""

    def test_valid_secret_references_pass(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "secure.toml"
        toml_file.write_text(_VALID_HTTP_WITH_SECRETS_TOML)

        registry = SecretRegistry({"API_TOKEN": "real-token-value"})
        # Should not raise.
        defn = load_tool_definition(toml_file, secret_registry=registry)

        assert defn.name == "secure_api"

    def test_unknown_secret_reference_hard_error(
        self, tmp_path: Path,
    ) -> None:
        toml_file = tmp_path / "secure.toml"
        toml_file.write_text(_VALID_HTTP_WITH_SECRETS_TOML)

        # Registry without API_TOKEN.
        registry = SecretRegistry({"OTHER_KEY": "some-value"})

        with pytest.raises(KeyError, match="API_TOKEN"):
            load_tool_definition(toml_file, secret_registry=registry)

    def test_no_registry_skips_validation(self, tmp_path: Path) -> None:
        """When no registry is provided, secret references are not validated."""
        toml_file = tmp_path / "secure.toml"
        toml_file.write_text(_VALID_HTTP_WITH_SECRETS_TOML)

        # Should not raise -- no registry means no validation.
        defn = load_tool_definition(toml_file, secret_registry=None)

        assert defn.name == "secure_api"


# ---------------------------------------------------------------------------
# Unknown keys (extra=forbid)
# ---------------------------------------------------------------------------


class TestUnknownKeys:
    """Pydantic extra=forbid rejects unknown fields."""

    def test_unknown_tool_section_key(self, tmp_path: Path) -> None:
        bad_toml = _VALID_HTTP_TOML.replace(
            "deferred = false",
            "deferred = false\nunknown_field = true",
        )
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="unknown_field"):
            load_tool_definition(toml_file)

    def test_unknown_execution_key(self, tmp_path: Path) -> None:
        bad_toml = _VALID_HTTP_TOML.replace(
            'method = "GET"',
            'method = "GET"\nretries = 3',
        )
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="retries"):
            load_tool_definition(toml_file)

    def test_unknown_top_level_section(self, tmp_path: Path) -> None:
        bad_toml = _VALID_HTTP_TOML + "\n[metadata]\nauthor = 'test'\n"
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="metadata"):
            load_tool_definition(toml_file)

    def test_unknown_param_key(self, tmp_path: Path) -> None:
        bad_toml = _VALID_HTTP_TOML.replace(
            'pattern = "^[A-Za-z ]+$"',
            'pattern = "^[A-Za-z ]+$"\nmin_length = 1',
        )
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="min_length"):
            load_tool_definition(toml_file)


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------


class TestMissingFields:
    """Missing required fields produce hard errors."""

    def test_missing_tool_section(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text("""\
format_version = 1

[execution]
type = "command"
executable = "echo"
arg_validation = true
timeout_ceiling = 30
""")

        with pytest.raises(ValueError, match="required field tool"):
            load_tool_definition(toml_file)

    def test_missing_execution_section(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text("""\
format_version = 1

[tool]
name = "test"
description = "test tool"
namespace = "custom.test"
deferred = false
""")

        with pytest.raises(ValueError, match="execution"):
            load_tool_definition(toml_file)

    def test_missing_name(self, tmp_path: Path) -> None:
        bad_toml = _VALID_COMMAND_TOML.replace('name = "run_linter"\n', "")
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="required field name"):
            load_tool_definition(toml_file)

    def test_missing_deferred(self, tmp_path: Path) -> None:
        bad_toml = _VALID_COMMAND_TOML.replace("deferred = false\n", "")
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="deferred"):
            load_tool_definition(toml_file)

    def test_missing_monty_limits(self, tmp_path: Path) -> None:
        bad_toml = """\
format_version = 1

[tool]
name = "test"
description = "test"
namespace = "custom.test"
deferred = false

[execution]
type = "monty"
code = "x = 1"
capabilities = []
"""
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="limits"):
            load_tool_definition(toml_file)

    def test_missing_command_arg_validation(self, tmp_path: Path) -> None:
        bad_toml = _VALID_COMMAND_TOML.replace("arg_validation = true\n", "")
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="arg_validation"):
            load_tool_definition(toml_file)

    def test_missing_command_timeout_ceiling(self, tmp_path: Path) -> None:
        bad_toml = _VALID_COMMAND_TOML.replace("timeout_ceiling = 120\n", "")
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="timeout_ceiling"):
            load_tool_definition(toml_file)


# ---------------------------------------------------------------------------
# Name collision
# ---------------------------------------------------------------------------


class TestNameCollision:
    """Duplicate tool names across files produce hard errors."""

    def test_name_collision_hard_error(self, tmp_tool_dir: Path) -> None:
        (tmp_tool_dir / "a.toml").write_text(_VALID_HTTP_TOML)
        # Second file with the same tool name.
        (tmp_tool_dir / "b.toml").write_text(_VALID_HTTP_TOML)

        with pytest.raises(ValueError, match="Duplicate data-defined tool name"):
            load_tool_definitions(tmp_tool_dir)


# ---------------------------------------------------------------------------
# File not found
# ---------------------------------------------------------------------------


class TestFileNotFound:
    """Non-existent paths produce clear errors."""

    def test_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError, match="Tool definition file"):
            load_tool_definition(Path("/nonexistent/tool.toml"))

    def test_missing_directory(self) -> None:
        with pytest.raises(FileNotFoundError, match="Tools directory"):
            load_tool_definitions(Path("/nonexistent/tools/"))


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


class TestParamValidation:
    """Parameter definition validation."""

    def test_invalid_regex_pattern(self, tmp_path: Path) -> None:
        bad_toml = _VALID_HTTP_TOML.replace(
            'pattern = "^[A-Za-z ]+$"',
            'pattern = "[invalid"',
        )
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="Invalid regex pattern"):
            load_tool_definition(toml_file)

    def test_invalid_param_type(self, tmp_path: Path) -> None:
        bad_toml = _VALID_HTTP_TOML.replace(
            'type = "string"',
            'type = "blob"',
            1,  # Replace only the first occurrence.
        )
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="not one of"):
            load_tool_definition(toml_file)


# ---------------------------------------------------------------------------
# Execution type discrimination
# ---------------------------------------------------------------------------


class TestExecutionDiscrimination:
    """The execution type discriminator resolves correctly."""

    def test_http_discriminated(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "tool.toml"
        toml_file.write_text(_VALID_HTTP_TOML)

        defn = load_tool_definition(toml_file)

        assert isinstance(defn.execution, HttpExecution)
        assert defn.execution.type == "http"

    def test_monty_discriminated(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "tool.toml"
        toml_file.write_text(_VALID_MONTY_TOML)

        defn = load_tool_definition(toml_file)

        assert isinstance(defn.execution, MontyExecution)
        assert defn.execution.type == "monty"

    def test_command_discriminated(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "tool.toml"
        toml_file.write_text(_VALID_COMMAND_TOML)

        defn = load_tool_definition(toml_file)

        assert isinstance(defn.execution, CommandExecution)
        assert defn.execution.type == "command"

    def test_unknown_type_rejected(self, tmp_path: Path) -> None:
        bad_toml = _VALID_COMMAND_TOML.replace(
            'type = "command"',
            'type = "unknown_engine"',
        )
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="unknown_engine"):
            load_tool_definition(toml_file)


# ---------------------------------------------------------------------------
# Empty tool name
# ---------------------------------------------------------------------------


class TestEmptyName:
    """Empty tool name is rejected."""

    def test_empty_name(self, tmp_path: Path) -> None:
        bad_toml = _VALID_COMMAND_TOML.replace(
            'name = "run_linter"',
            'name = ""',
        )
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match=r"tool.name"):
            load_tool_definition(toml_file)

    def test_whitespace_only_name(self, tmp_path: Path) -> None:
        bad_toml = _VALID_COMMAND_TOML.replace(
            'name = "run_linter"',
            'name = "   "',
        )
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text(bad_toml)

        with pytest.raises(ValueError, match="Tool name must not be empty"):
            load_tool_definition(toml_file)
