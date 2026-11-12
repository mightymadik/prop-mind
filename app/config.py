from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Секреты и окружение — только из env / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://prop:prop@localhost:5432/prop"
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    config_path: str = "config/demo.yaml"
    history_secret: str = ""  # HMAC подписи реплик ассистента в истории (v2); пусто — подписи не принимаются


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
    cache_write: float | None = None  # запись в кэш промпта (5 мин); не задано — 1,25 × input
    cache_read: float | None = None  # чтение кэша; не задано — 0,1 × input

    def write_price(self) -> float:
        return self.cache_write if self.cache_write is not None else self.input * 1.25

    def read_price(self) -> float:
        return self.cache_read if self.cache_read is not None else self.input * 0.1


class LimitsConfig(BaseModel):
    daily_budget_usd: float
    requests_per_minute_per_ip: int
    timezone: str = "Asia/Almaty"


class SiteConfig(BaseModel):
    origins: list[str] = []


class ModerationConfig(BaseModel):
    allowed_contacts: list[str] = []  # контакты, которые ответу можно выводить (прод: отдел продаж); иначе вырезаются
    stuffing_min_repeats: int = 4  # набивка: одна основа слова повторяется не меньше стольких раз…
    stuffing_min_ratio: float = 0.05  # …и доля повторов в описании не меньше этой


class MortgageProgram(BaseModel):
    """Условия программы для калькулятора; модель их не задаёт, только выбирает программу по ключу."""

    label: str
    rate_pct: float = Field(ge=0)  # годовая ставка, %; 0 — рассрочка (равные платежи)
    min_down_pct: float = Field(ge=0, lt=100)  # минимальный взнос, % цены
    max_years: int = Field(gt=0)
    max_loan_kzt: int | None = None  # лимит суммы кредита; None — без лимита
    as_of: str  # дата, на которую указаны условия
    source: str  # откуда условия («пример, не оферта» — в демо)


class AgentConfig(BaseModel):
    enabled: bool = False  # POST /agent; выключен — 404
    daily_budget_usd: float = 1.0  # из общего дневного бюджета — не больше стольких на агента (mode='v2')
    price_stats_min_count: int = 5  # меньше квартир в выборке — «мало данных» вместо медианы
    max_tool_calls: int = 6  # вызовов инструментов за ход; после — tool_choice: none
    max_search_calls: int = 3  # из них search_listings
    history_max_turns: int = 6  # последние реплики диалога, которые видит агент
    history_max_chars: int = 6000  # и не больше стольких символов текста реплик — старые выпадают первыми
    history_assistant_chars: int = 600  # подписанная реплика ассистента обрезается до стольких символов
    history_signed_ttl_hours: int = 24  # подпись старше — реплика считается неподписанной


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
    price_coefs: dict[str, float]  # поправки к ставке за м² при генерации синтетики


class AppConfig(BaseModel):
    """Несекретные настройки из config/*.yaml."""

    source: str
    catalog: CatalogConfig
    cities: list[str]
    districts: dict[str, list[str]]
    field_map: dict[str, str]
    models: dict[str, str]
    embeddings: EmbeddingsConfig
    rerank: RerankConfig
    answer: AnswerConfig
    pricing: dict[str, Price]  # модель → цена за 1 млн токенов
    limits: LimitsConfig
    sites: dict[str, SiteConfig]
    moderation: ModerationConfig = ModerationConfig()
    mortgage: dict[str, MortgageProgram] = {}  # программа → условия (калькулятор ипотеки агента)
    agent: AgentConfig = AgentConfig()


def load_config(path: str | Path) -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


@lru_cache
def get_settings() -> Settings:
    return Settings()
