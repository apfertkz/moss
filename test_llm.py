# -*- coding: utf-8 -*-
"""
Проверки повторов при сбоях модели.

    python test_llm.py
"""

import sys
import types

from trainer import llm

FAILS = []


def check(name, cond, extra=""):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


class Overloaded(Exception):
    pass
Overloaded.__name__ = "OverloadedError"


class BadRequest(Exception):
    def __init__(self):
        super().__init__("invalid model name")
        self.status_code = 400
BadRequest.__name__ = "APIStatusError"


def client_that(fails, exc):
    """Клиент, который падает первые N раз, потом отвечает."""
    state = {"n": 0}

    class Msgs:
        def create(self, **kw):
            state["n"] += 1
            if state["n"] <= fails:
                raise exc()
            return "ответ"

    c = types.SimpleNamespace(messages=Msgs())
    c._calls = state
    return c


def main():
    llm.BASE_DELAY = 0.01   # в тестах ждать нечего
    llm.MAX_DELAY = 0.02

    print("\n1. Временные сбои")
    c = client_that(1, Overloaded)
    check("одна перегрузка переживается", llm.create(c) == "ответ")
    check("была ровно одна повторная попытка", c._calls["n"] == 2, c._calls["n"])

    c = client_that(2, Overloaded)
    check("две подряд тоже", llm.create(c) == "ответ", )
    check("попыток три", c._calls["n"] == 3, c._calls["n"])

    print("\n2. Когда сдаёмся")
    c = client_that(99, Overloaded)
    try:
        llm.create(c)
        check("после лимита ошибка пробрасывается", False)
    except Exception as e:
        check("после лимита ошибка пробрасывается", type(e).__name__ == "OverloadedError")
    check("больше трёх попыток не делаем", c._calls["n"] == llm.ATTEMPTS, c._calls["n"])

    print("\n3. Что повторять бессмысленно")
    c = client_that(99, BadRequest)
    try:
        llm.create(c)
        check("ошибка запроса не повторяется", False)
    except Exception:
        check("ошибка запроса не повторяется", c._calls["n"] == 1, c._calls["n"])

    print("\n4. Распознавание")
    check("перегрузка — временная", llm._is_retriable(Overloaded()))
    check("таймаут по тексту — временный", llm._is_retriable(Exception("Read timed out")))
    check("529 — временная", llm._is_retriable(Exception("Error 529")))
    check("неверный ключ — постоянная", not llm._is_retriable(Exception("invalid x-api-key")))

    print("\n5. Успех с первого раза без пауз")
    c = client_that(0, Overloaded)
    check("лишних вызовов нет", llm.create(c) == "ответ" and c._calls["n"] == 1)

    print("\n" + "=" * 58)
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}: " + "; ".join(FAILS))
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    main()
