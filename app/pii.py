"""Маскирование ПДн в тексте запроса перед записью в query_log (см. DESIGN.md, «Безопасность»)."""

import re

# порядок важен: email и длинные цифровые последовательности — до телефонов
PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\b(?:\d[ -]?){15,18}\d\b"), "[карта]"),  # 16–19 цифр
    (re.compile(r"\b\d{12}\b"), "[ИИН]"),
    (re.compile(r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b"), "[телефон]"),
]


def mask_pii(text: str) -> str:
    for rx, repl in PATTERNS:
        text = rx.sub(repl, text)
    return text
