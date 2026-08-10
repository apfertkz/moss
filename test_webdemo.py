# -*- coding: utf-8 -*-
"""
Проверки демо на сайте: сценарий, лимиты, отказы.

Модель не дёргаем — подменяем заглушкой. Проверяем то, что ломается в
реальности: рубежи защиты, границы диалога и поведение при сбое модели.

    python test_webdemo.py
"""

import asyncio
import json
import sys
import types

FAILS = []


def check(name, cond, extra=""):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


class FakeDB:
    """База в памяти: только то, что нужно демо."""

    def __init__(self):
        self.rows = []
        self.spent = 0.0

    def query(self, sql, params=(), one=False):
        s = " ".join(sql.split())
        if "SUM(cost_usd)" in s:
            return {"s": self.spent}
        if "COUNT(*) AS n FROM web_demo" in s:
            return {"n": len([r for r in self.rows if r["ip"] == params[0]])}
        return None if one else []

    def execute(self, sql, params=(), returning=False):
        s = " ".join(sql.split())
        if "INSERT INTO web_demo" in s:
            self.rows.append({"token": params[0], "ip": params[1],
                              "niche": params[2], "answers": params[3]})
        elif "UPDATE web_demo" in s:
            self.rows.append({"update": params})
        return None


PROFILE = {
    "id": "test", "title": "Тестовая ниша",
    "product_context": "Продаём тестовый продукт очень подробно. " * 8,
    "currency": "тенге",
    "statuses": [{"id": f"s{i}", "title": f"Тип {i}", "context": "Описание типа клиента"}
                 for i in range(4)],
    "requests": [f"Запрос {i}" for i in range(6)],
}


class FakeResp:
    def __init__(self, text):
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.usage = types.SimpleNamespace(
            input_tokens=100, output_tokens=50,
            cache_creation_input_tokens=0, cache_read_input_tokens=0)


async def main():
    from trainer import db as real_db, webdemo, engine, llm

    fake = FakeDB()
    real_db.query = fake.query
    real_db.execute = fake.execute
    webdemo.TURNSTILE_SECRET = ""      # на машине разработчика проверки нет

    calls = {"n": 0}

    def fake_create(client, **kw):
        calls["n"] += 1
        if kw.get("system") is webdemo.PROFILE_SYSTEM or (
                isinstance(kw.get("system"), str) and "профиль ниши" in kw["system"]):
            return FakeResp(json.dumps(PROFILE, ensure_ascii=False))
        return FakeResp(json.dumps({
            "buyer_messages": ["Сколько стоит?"],
            "deal_state": "active", "stage": "contact", "coach_note": "",
        }, ensure_ascii=False))

    llm.create = fake_create
    engine.llm.create = fake_create

    print("\n1. Полный проход")
    r = await webdemo.start(None, {"product": "мох на стену", "audience": "рестораны",
                                   "price": "350 000", "pain": "пропадают"}, "1.1.1.1")
    sid = r["sid"]
    check("клиент собран", bool(r["client"]["name"]), r)
    check("ниша подхвачена", r["client"]["niche"] == "Тестовая ниша", r["client"])
    check("первое сообщение пришло", bool(r["messages"]))
    check("счётчик реплик выставлен", r["left"] == webdemo.MAX_TURNS)

    out = await webdemo.say(None, sid, "Здравствуйте! Расскажите, для какого помещения?")
    check("клиент ответил", bool(out["messages"]))
    check("остаток уменьшился", out["left"] == webdemo.MAX_TURNS - 1, out["left"])
    check("диалог не закончен", out["over"] is False)

    print("\n2. Лимит реплик")
    for _ in range(webdemo.MAX_TURNS - 1):
        out = await webdemo.say(None, sid, "Ещё вопрос по вашей задаче?")
    check("на последней реплике диалог закрывается", out["over"] is True, out)
    check("остатка нет", out["left"] == 0, out["left"])

    try:
        await webdemo.say(None, sid, "Ещё одно")
        check("после лимита ход не проходит", False)
    except ValueError:
        check("после лимита ход не проходит", True)

    print("\n3. Разбор и заявка")
    fin = await webdemo.finish(None, sid, contact="+7 700 000 00 00", name="Алекс")
    check("разбор пришёл", bool(fin["verdict"]), fin)
    check("сессия закрыта", sid not in webdemo._live)
    saved = [r for r in fake.rows if "update" in r]
    check("заявка сохранена", bool(saved), fake.rows[-1])
    check("контакт записан", any("+7 700 000 00 00" in str(r["update"]) for r in saved))

    print("\n4. Лимит по адресу")
    webdemo.PER_IP_DAY = 2
    fake.rows = [{"ip": "2.2.2.2"}, {"ip": "2.2.2.2"}]
    try:
        await webdemo.start(None, {"product": "a", "audience": "b", "price": "c", "pain": "d"},
                            "2.2.2.2")
        check("третья попытка с адреса отклонена", False)
    except ValueError as e:
        check("третья попытка с адреса отклонена", True)
        check("отказ мягкий и ведёт в бота", "бот" in str(e).lower(), str(e))

    print("\n5. Дневной потолок расхода")
    fake.rows = []
    fake.spent = webdemo.DAILY_USD + 1
    try:
        await webdemo.start(None, {"product": "a", "audience": "b", "price": "c", "pain": "d"},
                            "3.3.3.3")
        check("при исчерпанном потолке демо не запускается", False)
    except ValueError as e:
        check("при исчерпанном потолке демо не запускается", True)
        check("объяснение внятное", "лимит" in str(e).lower(), str(e))
    fake.spent = 0

    print("\n6. Сбой модели не роняет демо")
    def broken(client, **kw):
        raise RuntimeError("модель недоступна")
    llm.create = broken
    engine.llm.create = broken

    r = await webdemo.start(None, {"product": "мох", "audience": "рестораны",
                                   "price": "350 000", "pain": "пропадают"}, "4.4.4.4")
    check("демо запустилось на запасном профиле", bool(r["sid"]), r)
    check("первое сообщение всё равно есть", bool(r["messages"]), r)

    print("\n7. Молчание клиента в демо не отыгрывается")
    llm.create = fake_create
    engine.llm.create = fake_create
    r = await webdemo.start(None, {"product": "мох", "audience": "рестораны",
                                   "price": "350 000", "pain": "пропадают"}, "5.5.5.5")
    s = webdemo._live[r["sid"]]
    await webdemo.say(None, r["sid"], "Первый ход")
    check("движку запрещено уводить клиента в молчание",
          s["silences"] >= engine.MAX_SILENCES, s["silences"])
    check("состояние silent наружу не выходит",
          not engine.may_go_silent(s))

    print("\n" + "=" * 58)
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}: " + "; ".join(FAILS))
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    asyncio.run(main())
