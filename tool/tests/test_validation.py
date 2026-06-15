from __future__ import annotations

import pytest

from orxt.protocols._tool import ToolError
from orxt.tool._validation import validate_args


class TestValidateArgs:
    """Tests for validate_args."""

    def test_valid_args(self) -> None:
        """Valid arguments pass without error."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["name"],
        }
        validate_args({"name": "test", "count": 5}, schema)

    def test_missing_required_field(self) -> None:
        """Missing required field raises ToolError."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        }
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            validate_args({}, schema)

    def test_wrong_type(self) -> None:
        """Wrong type raises ToolError."""
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
            },
        }
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            validate_args({"count": "not_a_number"}, schema)

    def test_extra_field_with_additional_properties_false(self) -> None:
        """Extra field with additionalProperties: false raises ToolError."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "additionalProperties": False,
        }
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            validate_args({"name": "test", "extra": "bad"}, schema)

    def test_nested_object_validation(self) -> None:
        """Nested object schema validates correctly."""
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                    },
                    "required": ["enabled"],
                },
            },
            "required": ["config"],
        }
        # Valid nested
        validate_args({"config": {"enabled": True}}, schema)
        # Invalid nested
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            validate_args({"config": {"enabled": "yes"}}, schema)

    def test_empty_args_empty_schema(self) -> None:
        """Empty args against empty schema passes."""
        validate_args({}, {})

    def test_array_type_validation(self) -> None:
        """Array type in schema validates correctly."""
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }
        validate_args({"items": ["a", "b", "c"]}, schema)
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            validate_args({"items": [1, 2, 3]}, schema)

    def test_enum_validation(self) -> None:
        """Enum constraint validates correctly."""
        schema = {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["read", "write"]},
            },
        }
        validate_args({"mode": "read"}, schema)
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            validate_args({"mode": "delete"}, schema)

    def test_error_is_tool_error(self) -> None:
        """Validation errors are ToolError, not jsonschema.ValidationError."""
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        with pytest.raises(ToolError):
            validate_args({"x": "not_int"}, schema)
