from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    name: str
    namespace: str
    description: str
    params_model: type
    result_model: type | None
    tags: frozenset[str]
    category: str
