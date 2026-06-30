from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True)
class SurfaceSpec:
    surface_id: str
    catalog_id: str
    theme: dict[str, Any] | None


class CreateSurface(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    surface_id: str
    catalog_id: str
    theme: dict[str, Any] | None = None
    send_data_model: bool = False


class UpdateComponents(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    surface_id: str
    components: list[dict[str, Any]]


class UpdateDataModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    surface_id: str
    path: str = "/"
    value: Any = None


class DeleteSurface(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    surface_id: str


type SurfaceOperation = CreateSurface | UpdateComponents | UpdateDataModel | DeleteSurface
