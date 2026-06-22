from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

from orxtra.agent import load_agent, load_categories
from orxtra.scheduler import load_workflow, validate_task_tree

if TYPE_CHECKING:
    from pathlib import Path

_VALIDATION_ERRORS = (ValueError, FileNotFoundError, KeyError, tomllib.TOMLDecodeError)


async def validate_agent(path: Path) -> list[str]:
    try:
        load_agent(path)
    except _VALIDATION_ERRORS as e:
        return [str(e)]
    return []


async def validate_workflow(path: Path) -> list[str]:
    try:
        config = load_workflow(path)
    except _VALIDATION_ERRORS as e:
        return [str(e)]
    return validate_task_tree(config)


async def validate_categories(path: Path) -> list[str]:
    try:
        load_categories(path)
    except _VALIDATION_ERRORS as e:
        return [str(e)]
    return []
