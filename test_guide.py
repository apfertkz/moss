# -*- coding: utf-8 -*-
"""
Проверки выдачи гайда: порядок сообщений, файл, тексты.
Бот подменяется заглушкой — проверяем последовательность вызовов.

    python test_guide.py
"""

import asyncio
import re
import sys
import types

from trainer import guide

FAILS = []


def check(name, cond, extra=""):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


class FakeBot:
    def __init__(self, fail_markdown=False):
        self.calls = []
        self.fail_markdown = fail_markdown

    async def send_message(self, chat_id, text, **kw):
        if self.fail_markdown and kw.get("parse_mode"):
            raise Exception("Bad Request: can't parse entities")
        self.calls.append(("message", chat_id, text, kw))

    async def send_document(self, chat_id, doc, **kw):
        self.calls.append(("document", chat_id, doc.filename, kw))


def main():
    print("\n1. Файл гайда")
    check("страница на месте", guide.guide_exists(), guide.GUIDE_FILE)
    body = open(guide.GUIDE_FILE, encoding="utf-8").read()
    check("это статическая вёрстка, а не сборка скриптом", "<section" in body)
    check("экранов двадцать", body.count("<section") == 20, body.count("<section"))
    check("имя файла человекочитаемое", guide.FILENAME == "algoritm-prodazh.html")

    print("\n2. Тексты")
    for label, txt in (("владельцу", guide.OWNER_TEXT),
                       ("менеджеру", guide.MANAGER_TEXT),
                       ("повторно", guide.REPEAT_TEXT)):
        # Ищем по границам слов: «бот» иначе находится внутри «работа».
        check(f"{label}: не упоминает тренажёр и бота",
              not re.search(r"\bбот\w*|\bтренаж\w*", txt.lower()))
        check(f"{label}: разметка сбалансирована", txt.count("*") % 2 == 0, txt.count("*"))
        check(f"{label}: без подчёркиваний, ломающих разметку", txt.count("_") == 0)
    check("владельцу сказано переслать менеджерам", "Перешлите менеджерам" in guide.OWNER_TEXT)
    check("владельцу дан способ проверить", "Квалификация" in guide.OWNER_TEXT)
    check("менеджеру обещана его выгода", "конверсию" in guide.MANAGER_TEXT)
    check("менеджеру сказано, что не про один товар",
          "не про конкретный товар" in guide.MANAGER_TEXT)
    check("тексты владельца и менеджера разные", guide.OWNER_TEXT != guide.MANAGER_TEXT)
    check("выбор текста по роли",
          guide.text_for(True) == guide.OWNER_TEXT and guide.text_for(False) == guide.MANAGER_TEXT)

    print("\n3. Порядок отправки")
    b = FakeBot()
    asyncio.run(guide.deliver(b, 555, guide.OWNER_TEXT))
    kinds = [c[0] for c in b.calls]
    check("ровно два отправления", len(b.calls) == 2, kinds)
    check("сначала пояснение, потом файл", kinds == ["message", "document"], kinds)
    check("файл с нужным именем", b.calls[1][2] == guide.FILENAME)
    check("у файла есть подпись", "браузере" in b.calls[1][3].get("caption", ""))
    check("адресат один и тот же", b.calls[0][1] == b.calls[1][1] == 555)

    print("\n4. Устойчивость")
    b = FakeBot(fail_markdown=True)
    asyncio.run(guide.deliver(b, 777, guide.MANAGER_TEXT))
    check("при отказе разметки текст уходит без неё", len(b.calls) == 2, [c[0] for c in b.calls])
    check("звёздочки убраны", "*" not in b.calls[0][2])
    check("файл всё равно отправлен", b.calls[1][0] == "document")

    real = guide.GUIDE_FILE
    guide.GUIDE_FILE = "/nope/missing.html"
    b = FakeBot()
    asyncio.run(guide.deliver(b, 999, guide.REPEAT_TEXT))
    check("без файла не падает, шлёт хотя бы текст", len(b.calls) == 1)
    guide.GUIDE_FILE = real

    print("\n" + "=" * 58)
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}: " + "; ".join(FAILS))
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    main()
