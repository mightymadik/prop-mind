"""Защита от prompt injection в данных объявлений: признаки вставки, набивки и контакты в ответе.

Описания пишут продавцы — это непроверенные данные. Модель ответа инструкции из них не выполняет
(промпт), а код дополнительно: помечает подозрительные объекты в query_log для модерации, убирает
набитое ключевыми словами описание из search_text и вырезает контакты и ссылки из ответа.
"""

import re
from collections import Counter

from app.config import ModerationConfig

CONTACT = re.compile(
    r"(?:https?://|www\.)\S+?(?=[.,;:!?)»\"]*(?:\s|$))|\b[\w-]+\.(?:kz|ru|com|net|org|example)\b|wa\.me/\S*|t\.me/\S*|(?<!\w)@[A-Za-z_]\w{3,}"
    r"|[\w.+-]+@[\w-]+\.[\w.-]+|(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b",
    re.I,
)
# обращение к ИИ + глагол-указание; или явные признаки вставки (поддельные роли, теги, номера [n], контакты).
# ponytail: регулярки ловят грубые вставки; на демо-описаниях — 0 ложных из 1048, перепроверить на данных прода
_ADDRESS = re.compile(
    r"(?<!\w)(ии|ai|ассистент\w*|модел[ьиуе]|нейросет\w*|chatgpt|claude|gpt|бот\w*|assistant|систем\w*|жи|көмекші\w*)(?!\w)",
    re.I,
)
_ORDER = re.compile(
    r"игнорир\w*|инструкц\w*|рекомендуй|порекомендуй|выведи|скажи|передай|упоминай|ответь|ответе|правил\w*"
    r"|нұсқау|елеме|ignore|instruction|rank|recommend",
    re.I,
)
_STRONG = re.compile(
    r"игнорируй|ignore (?:all|previous|the)|промпт|prompt|начни ответ|начни с\b|системн\w* (?:уведомлен|сообщен)"
    r"|</?\s*description|(?:^|\n)\s*(?:human|assistant|system|пользователь|ассистент)\s*:|\[\d+\]",
    re.I,
)
_WORD = re.compile(r"[^\W\d_]{3,}")
_SPACE = re.compile(r"\s")
HOLD = 40  # хвост стрима, который держим: недописанный телефон с пробелами короче


def looks_injected(text: str) -> bool:
    """Описание похоже на вставку для модели или содержит контакты."""
    return bool(_STRONG.search(text) or CONTACT.search(text) or (_ADDRESS.search(text) and _ORDER.search(text)))


def is_stuffed(text: str, cfg: ModerationConfig) -> bool:
    """Набивка ключевыми словами: одна основа (первые 5 букв) повторяется часто и повторов много."""
    stems = [w[:5] for w in _WORD.findall(text.lower())]
    if not stems:
        return False
    top = Counter(stems).most_common(1)[0][1]
    repeat_ratio = 1 - len(set(stems)) / len(stems)
    return top >= cfg.stuffing_min_repeats and repeat_ratio >= cfg.stuffing_min_ratio


def index_description(text: str, cfg: ModerationConfig) -> str:
    """Описание для search_text: набитое ключевыми словами не индексируется — объект ищется по полям."""
    return "" if is_stuffed(text, cfg) else text


def _norm(s: str) -> str:
    return re.sub(r"[\s()\-]", "", s.lower())


def strip_contacts(text: str, allowed: list[str]) -> str:
    """Контакты и ссылки вырезаются; оставляются только разрешённые конфигом (allowed_contacts)."""
    ok = {_norm(a) for a in allowed}
    return CONTACT.sub(lambda m: m.group(0) if _norm(m.group(0)) in ok else "", text)


class ContactFilter:
    """То же для стрима: кусок отдаётся, только когда контакт в нём точно дописан. Держит хвост
    в HOLD символов и режет по пробелу вне найденного контакта (в телефоне бывают пробелы)."""

    def __init__(self, allowed: list[str]):
        self.allowed = allowed
        self.buf = ""

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        spans = [m.span() for m in CONTACT.finditer(self.buf)]
        limit = len(self.buf) - HOLD
        cut = 0
        for m in _SPACE.finditer(self.buf):
            if m.start() > limit:
                break
            if not any(a < m.start() < b for a, b in spans):
                cut = m.start()
        out, self.buf = self.buf[:cut], self.buf[cut:]
        return strip_contacts(out, self.allowed)

    def flush(self) -> str:
        out, self.buf = self.buf, ""
        return strip_contacts(out, self.allowed)
