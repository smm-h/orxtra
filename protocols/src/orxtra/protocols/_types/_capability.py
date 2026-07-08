from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


@dataclass(frozen=True)
class Capability:
    name: str
    namespace: str
    description: str
    params_model: type[BaseModel]
    result_model: type | None
    tags: frozenset[str]
    category: str
