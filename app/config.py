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


class AnswerModelConfig(BaseModel):
    thinking: dict  # как выключить рассуждения у этой модели (у Sonnet 5.5 и Haiku 5.5 по-разному)
    fallbacks: str | None = None  # серверный фолбэк при отказе модели; "default" — по рекомендации Anthropic


class AnswerConfig(BaseModel):
    model: str  # модель ответа в продукте
    max_tokens: int = 1024
    top_k: int = 10  # сколько объектов передаём модели
    models: dict[str, AnswerModelConfig]  # настройки для каждой модели, которую можно выбрать


class Price(BaseModel):
    input: float  # $ за 1 млн токенов
    output: float


class LimitsConfig(BaseModel):
    daily_budget_usd: float
    requests_per_minute_per_ip: int
    timezone: str = "Asia/Almaty"


class SiteConfig(BaseModel):
    origins: list[str] = []


class ModerationConfig(BaseModel):
    allowed_contacts: list[str] = []  # контакты, которые ответу можно выводить (прод: отдел продаж); иначе вырезаются
    stuffing_min_repeats: int = 3  # набивка: одна основа слова повторяется не меньше стольких раз…
    stuffing_min_ratio: float = 0.15  # …и доля повторов в описании не меньше этой


class CatalogConfig(BaseModel):
    """Новостройки: ЖК → квартиры. Словари — ключ → как пишем в тексте (search_text, промпты, интерфейс)."""

    complexes_path: str
    units_path: str
    price_calibration_path: str  # ₸ за м² по городу, району и классу (data/price_calibration.csv)
    classes: dict[str, str]
    complex_features: dict[str, str]
    payment: dict[str, str]
    finishing: dict[str, str]
    windows: dict[str, str]
    views: dict[str, str]
    orientations: dict[str, str]


class AppConfig(BaseModel):
    """Несекретные настройки из config/*.yaml."""

    source: str
    data_path: str
    catalog: CatalogConfig
    cities: list[str]
    districts: dict[str, list[str]]
    field_map: dict[str, str]
    features: dict[str, str]
    models: dict[str, str]
    embeddings: EmbeddingsConfig
    rerank: RerankConfig
    answer: AnswerConfig
    pricing: dict[str, Price]  # модель → цена за 1 млн токенов
    limits: LimitsConfig
    sites: dict[str, SiteConfig]
    moderation: ModerationConfig = ModerationConfig()


def load_config(path: str | Path) -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


@lru_cache
def get_settings() -> Settings:
    return Settings()
