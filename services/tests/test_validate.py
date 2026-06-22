from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from orxtra.services._validate import (
    validate_agent,
    validate_categories,
    validate_workflow,
)


@pytest.mark.asyncio
async def test_validate_agent_valid() -> None:
    with patch("orxtra.services._validate.load_agent") as mock_load:
        mock_load.return_value = MagicMock()

        result = await validate_agent(Path("/agents/coder.toml"))

        assert result == []
        mock_load.assert_called_once_with(Path("/agents/coder.toml"))


@pytest.mark.asyncio
async def test_validate_agent_invalid() -> None:
    with patch("orxtra.services._validate.load_agent") as mock_load:
        mock_load.side_effect = ValueError("missing required field 'name'")

        result = await validate_agent(Path("/agents/bad.toml"))

        assert len(result) == 1
        assert "missing required field 'name'" in result[0]


@pytest.mark.asyncio
async def test_validate_agent_missing() -> None:
    with patch("orxtra.services._validate.load_agent") as mock_load:
        mock_load.side_effect = FileNotFoundError("not found: /agents/gone.toml")

        result = await validate_agent(Path("/agents/gone.toml"))

        assert len(result) == 1
        assert "not found" in result[0]


@pytest.mark.asyncio
async def test_validate_workflow_valid() -> None:
    mock_config = MagicMock()
    with (
        patch("orxtra.services._validate.load_workflow") as mock_load,
        patch("orxtra.services._validate.validate_task_tree") as mock_validate,
    ):
        mock_load.return_value = mock_config
        mock_validate.return_value = []

        result = await validate_workflow(Path("/workflows/deploy.toml"))

        assert result == []
        mock_load.assert_called_once_with(Path("/workflows/deploy.toml"))
        mock_validate.assert_called_once_with(mock_config)


@pytest.mark.asyncio
async def test_validate_workflow_invalid() -> None:
    with patch("orxtra.services._validate.load_workflow") as mock_load:
        mock_load.side_effect = ValueError("invalid workflow structure")

        result = await validate_workflow(Path("/workflows/broken.toml"))

        assert len(result) == 1
        assert "invalid workflow structure" in result[0]


@pytest.mark.asyncio
async def test_validate_workflow_tree_errors() -> None:
    mock_config = MagicMock()
    with (
        patch("orxtra.services._validate.load_workflow") as mock_load,
        patch("orxtra.services._validate.validate_task_tree") as mock_validate,
    ):
        mock_load.return_value = mock_config
        mock_validate.return_value = ["cycle detected", "missing dependency: build"]

        result = await validate_workflow(Path("/workflows/cycle.toml"))

        assert len(result) == 2
        assert "cycle detected" in result
        assert "missing dependency: build" in result


@pytest.mark.asyncio
async def test_validate_categories_valid() -> None:
    with patch("orxtra.services._validate.load_categories") as mock_load:
        mock_load.return_value = MagicMock()

        result = await validate_categories(Path("/categories.toml"))

        assert result == []
        mock_load.assert_called_once_with(Path("/categories.toml"))


@pytest.mark.asyncio
async def test_validate_categories_missing() -> None:
    with patch("orxtra.services._validate.load_categories") as mock_load:
        mock_load.side_effect = FileNotFoundError("not found: /categories.toml")

        result = await validate_categories(Path("/categories.toml"))

        assert len(result) == 1
        assert "not found" in result[0]


@pytest.mark.asyncio
async def test_validate_categories_invalid() -> None:
    with patch("orxtra.services._validate.load_categories") as mock_load:
        mock_load.side_effect = ValueError("duplicate category: backend")

        result = await validate_categories(Path("/categories.toml"))

        assert len(result) == 1
        assert "duplicate category: backend" in result[0]
