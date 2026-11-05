from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Секреты и окружение — только из env / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://prop:prop@localhost:5432/prop"
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    config_path: str = "config/demo.yaml"


class EmbeddingsConfig(BaseModel):
    provider: str
    model: str
    dim: int
    batch_size: int = 128


class RerankConfig(BaseModel):
    provider: str
    model: str
    candidates: int = 50  # сколько кандидатов гибрида переранжировать


class AppConfig(BaseModel):
    """Несекретные настройки из config/*.yaml."""

    source: str
    data_path: str
    cities: list[str]
    districts: dict[str, list[str]]
    field_map: dict[str, str]
    features: dict[str, str]
    models: dict[str, str]
    embeddings: EmbeddingsConfig
    rerank: RerankConfig
    limits: dict[str, float]


def load_config(path: str | Path) -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


@lru_cache
def get_settings() -> Settings:
    return Settings()
