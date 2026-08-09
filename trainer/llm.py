# -*- coding: utf-8 -*-
"""
Обращение к модели с повторами.

Anthropic иногда отвечает перегрузкой или рвёт соединение. Раньше любая
такая осечка обрывала тренировку: менеджер видел «Тренажёр споткнулся»
посреди диалога с клиентом и терял нить. Одна повторная попытка через
пару секунд закрывает подавляющее большинство таких случаев.

Повторяем только то, что имеет смысл повторять: перегрузку, таймаут, сеть
и пятисотые. Ошибку в самом запросе — неверная модель, слишком длинный
контекст, отозванный ключ — повторять бессмысленно, она вернётся такой же.
"""

import logging
import random
import time

log = logging.getLogger(__name__)

ATTEMPTS = 3
BASE_DELAY = 1.5      # секунды до первой повторной попытки
MAX_DELAY = 12.0

# Что считаем временным. Проверяем по имени класса и тексту, чтобы не зависеть
# от версии библиотеки: набор классов в ней меняется от релиза к релизу.
_RETRIABLE_NAMES = (
    "APIConnectionError", "APITimeoutError", "APIStatusError",
    "InternalServerError", "RateLimitError", "OverloadedError",
    "ServiceUnavailableError",
)
_RETRIABLE_TEXT = (
    "overloaded", "rate limit", "timeout", "timed out", "temporarily",
    "connection", "502", "503", "504", "529",
)


def _is_retriable(exc):
    name = type(exc).__name__
    if name in _RETRIABLE_NAMES:
        # Явно невосстановимые статусы отсеиваем: повтор вернёт то же самое.
        status = getattr(exc, "status_code", None)
        if status and 400 <= status < 500 and status not in (408, 409, 429):
            return False
        return True
    text = str(exc).lower()
    return any(t in text for t in _RETRIABLE_TEXT)


def create(client, **kwargs):
    """
    Вызвать модель, повторив при временной ошибке.

    Пауза растёт вдвое и слегка размывается случайной добавкой: если
    перегрузка накрыла нескольких менеджеров разом, их повторы не должны
    приходить одной волной.
    """
    delay = BASE_DELAY
    last = None

    for attempt in range(1, ATTEMPTS + 1):
        try:
            return client.messages.create(**kwargs)
        except Exception as e:
            last = e
            if attempt == ATTEMPTS or not _is_retriable(e):
                raise
            wait = min(MAX_DELAY, delay) * (1 + random.random() * 0.3)
            log.warning("Модель не ответила (%s), попытка %s из %s через %.1f с",
                        type(e).__name__, attempt, ATTEMPTS, wait)
            time.sleep(wait)
            delay *= 2

    raise last  # недостижимо, но пусть будет явным
