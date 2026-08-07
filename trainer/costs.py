# -*- coding: utf-8 -*-
"""
Стоимость обращений к модели.

Цены — официальный прайс Anthropic, $ за 1 млн токенов, на 07.08.2026.
Источник: platform.claude.com/docs/en/about-claude/pricing

Зачем это в коде: без учёта фактического расхода маржа по клиенту неизвестна,
а лимиты не на чем строить. Каждый вызов пишется в usage_log.
"""

# модель -> (входящие, исходящие, запись кеша, чтение кеша)
PRICES = {
    "claude-opus-5":   (5.0, 25.0, 6.25, 0.50),
    "claude-sonnet-5": (3.0, 15.0, 3.75, 0.30),
    "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.10),
}
_FALLBACK = PRICES["claude-sonnet-5"]


def usage_dict(resp):
    """Вытащить расход токенов из ответа SDK в обычный словарь."""
    u = getattr(resp, "usage", None)
    if u is None:
        return {"input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0}
    return {
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
        "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
    }


def cost_usd(model, usage):
    """Стоимость одного вызова в долларах."""
    pin, pout, pw, pr = PRICES.get(model, _FALLBACK)
    return (
        usage["input_tokens"] * pin
        + usage["output_tokens"] * pout
        + usage["cache_write"] * pw
        + usage["cache_read"] * pr
    ) / 1_000_000


def add(a, b):
    """Сложить два словаря расхода."""
    return {k: a.get(k, 0) + b.get(k, 0) for k in
            ("input_tokens", "output_tokens", "cache_write", "cache_read")}
