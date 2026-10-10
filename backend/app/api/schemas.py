from typing import Optional

from pydantic import BaseModel


class ModelConnectionCreate(BaseModel):
    name: str
    provider_type: str
    base_url: str
    api_key: str
    available_models: list[str] = []


class ModelConnectionUpdate(BaseModel):
    name: Optional[str] = None
    provider_type: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    available_models: Optional[list[str]] = None
