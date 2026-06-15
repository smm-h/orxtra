from __future__ import annotations

from pathlib import Path

from orxt.agent import load_agent, load_categories
from orxt.scheduler import load_workflow, validate_task_tree


async def validate_agent(path: Path) -> list[str]:
    try:
        load_agent(path)
    except Exception as e:
        return [str(e)]
    return []


async def validate_workflow(path: Path) -> list[str]:
    try:
        config = load_workflow(path)
    except Exception as e:
        return [str(e)]
    return validate_task_tree(config)


async def validate_categories(path: Path) -> list[str]:
    try:
        load_categories(path)
    except Exception as e:
        return [str(e)]
    return []
