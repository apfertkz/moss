# -*- coding: utf-8 -*-
"""
Проверки тренировочной комнаты: пропуск, лимиты, ход тренировки, память.

Ни базы, ни модели, ни бота здесь нет — всё подменено заглушками. Проверяем
то, что ломается в реальности: чужой пропуск, исчерпанный лимит, брошенная
тренировка и разбор, который не должен показывать менеджеру подсказки
клиента.

    python test_webroom.py
"""

import asyncio
import json
import os
import sys
import types

# Запускаться тест должен из любой папки: путь берём от самого файла.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILS = []


def check(name, cond, extra=""):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


PROFILE = {
    "id": "test", "title": "Тестовая ниша",
    "product_context": "Продаём тестовый продукт очень подробно. " * 8,
    "currency": "тенге",
    "statuses": [{"id": f"s{i}", "title": f"Тип {i}", "context": "Описание типа клиента"}
                 for i in range(4)],
    "requests": [f"Запрос {i}" for i in range(6)],
}

USER = {"id": 7, "telegram_id": 555, "company_id": 42, "role": "manager",
        "active": True, "full_name": "Менеджер Иван", "username": "ivan",
        "company_status": "active", "sessions_used": 3, "session_limit": 200,
        "expires_at": None}


class FakeResp:
    def __init__(self, text):
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.usage = types.SimpleNamespace(
            input_tokens=100, output_tokens=50,
            cache_creation_input_tokens=0, cache_read_input_tokens=0)


class FakeStore:
    """Общая память бота и комнаты — по одной активной тренировке на человека."""

    def __init__(self):
        self.data = {}

    def get(self, telegram_id):
        # Через базу состояние проходит как JSON, поэтому кортежи в переписке
        # возвращаются списками. Повторяем это, иначе тест был бы добрее жизни.
        raw = self.data.get(telegram_id)
        return json.loads(json.dumps(raw, ensure_ascii=False)) if raw else None

    def put(self, telegram_id, company_id, session):
        self.data[telegram_id] = session

    def drop(self, telegram_id):
        self.data.pop(telegram_id, None)


class FakeRequest:
    def __init__(self, cookies):
        self.cookies = cookies


async def main():
    from trainer import webroom, engine, llm, tenancy, stats, store, niche_loader

    store_ = FakeStore()
    store.get, store.put, store.drop = store_.get, store_.put, store_.drop
    webroom.store = store

    recorded = {"sessions": [], "consumed": 0, "usage": []}
    stats.record_session = lambda user, scenario, result, turns, transcript=None: (
        recorded["sessions"].append({"result": result, "turns": turns,
                                     "transcript": transcript}) or 1)
    stats.record_usage = lambda *a: recorded["usage"].append(a)

    def consume(company_id):
        recorded["consumed"] += 1
        return 4, 200
    tenancy.consume_session = consume
    tenancy.get_user = lambda tg: dict(USER) if tg == USER["telegram_id"] else None
    tenancy.get_company = lambda cid: {"title": "ООО Тест", "sessions_used": 3,
                                       "session_limit": 200}
    tenancy.check_can_train = lambda u: True
    niche_loader.active_profile = lambda company_id: PROFILE

    replies = {"state": "active"}

    def fake_create(client, **kw):
        return FakeResp(json.dumps({
            "buyer_messages": ["Сколько стоит?"],
            "deal_state": replies["state"], "silence_hours": 3,
            "stage": "contact", "coach_note": "",
        }, ensure_ascii=False))

    llm.create = fake_create
    engine.llm.create = fake_create

    print("\n1. Пропуск менеджера")
    token = webroom.make_token(42, 555)
    check("свой пропуск читается", webroom.read_token(token) == (42, 555))
    check("подделанная подпись отвергнута",
          webroom.read_token(token[:-3] + "aaa") is None)
    check("чужой формат отвергнут", webroom.read_token("admin.zzz") is None)

    was = webroom.SESSION_DAYS
    webroom.SESSION_DAYS = -1
    check("просроченный пропуск отвергнут",
          webroom.read_token(webroom.make_token(42, 555)) is None)
    webroom.SESSION_DAYS = was

    check("компания из куки сверяется с базой",
          webroom.identity(FakeRequest({webroom.COOKIE: webroom.make_token(999, 555)})) is None)
    check("свой вход опознан",
          (webroom.identity(FakeRequest({webroom.COOKIE: token}) ) or {}).get("telegram_id") == 555)
    check("без куки никого нет", webroom.identity(FakeRequest({})) is None)

    USER["active"] = False
    check("отключённый менеджер не входит",
          webroom.identity(FakeRequest({webroom.COOKIE: token})) is None)
    USER["active"] = True

    print("\n2. Полный проход тренировки")
    r = await webroom.start(None, dict(USER))
    check("клиент собран", bool(r["client"]["name"]), r["client"])
    check("ниша компании подхвачена", r["client"]["niche"] == "Тестовая ниша", r["client"])
    check("первая реплика клиента есть", len(r["transcript"]) >= 1, r["transcript"])
    check("вступление для менеджера есть", bool(r.get("intro")))
    check("сценарий наружу не уходит", "scenario" not in r and "profile" not in r)
    check("тренировка сохранена в общей памяти", 555 in store_.data)

    out = await webroom.say(None, dict(USER), "Здравствуйте! Для какого помещения?")
    check("клиент ответил", bool(out["messages"]), out)
    check("ход засчитан", out["turns"] == 1, out["turns"])
    check("переписка растёт", len(out["transcript"]) >= 3, out["transcript"])
    check("тренировка ещё не закрыта", out["over"] is False)
    check("лимит пока не списан", recorded["consumed"] == 0)

    print("\n3. Разбор и списание")
    replies["state"] = "won"
    out = await webroom.say(None, dict(USER), "Готов оформить сегодня?")
    check("закрытая сделка завершает тренировку", out["over"] is True, out)

    fin = await webroom.finish(None, dict(USER))
    check("разбор пришёл", bool(fin["verdict"]), fin)
    check("исход записан как выигрыш", fin["result"] == "won", fin)
    check("тренировка ушла в историю", len(recorded["sessions"]) == 1, recorded["sessions"])
    check("переписка сохранена целиком",
          len(recorded["sessions"][0]["transcript"]) >= 4)
    check("лимит списан ровно один раз", recorded["consumed"] == 1)
    check("активная тренировка закрыта", 555 not in store_.data)
    replies["state"] = "active"

    print("\n4. Брошенная тренировка не стоит денег")
    await webroom.start(None, dict(USER))
    await webroom.say(None, dict(USER), "Первый ход и ушёл пить кофе")
    check("лимит не тронут, пока нет разбора", recorded["consumed"] == 1)
    check("тренировка ждёт в памяти", 555 in store_.data)

    print("\n5. Продолжение начатого в боте")
    seen = webroom._view(store_.get(555))
    check("переписка отдаётся браузеру", len(seen["transcript"]) >= 2, seen["transcript"])
    check("подсказки клиента не отдаются", "scenario" not in seen)
    out = await webroom.say(None, dict(USER), "Вернулся, продолжаем")
    check("ход поверх поднятой из базы тренировки прошёл", out["turns"] == 2, out["turns"])

    print("\n6. Молчание клиента")
    replies["state"] = "silent"
    s = store_.data[555]
    s["silences"] = 0
    s["turns"] = engine.SILENCE_MIN_TURN + 1
    # Уход в молчание движок разыгрывает жребием. В тесте жребий не нужен:
    # проверяем не то, пропадёт ли клиент, а что комната делает, когда он
    # пропал. Флаг движка снимает случайность.
    s["allow_silent_now"] = True
    out = await webroom.say(None, dict(USER), "Ну что решили?")
    check("молчание отыграно, а не подменено ответом", out["state"] == "silent", out["state"])
    check("молчание названо словами в переписке",
          out["transcript"][-1]["role"] == "system", out["transcript"][-1])
    check("реплик клиента при молчании нет", out["messages"] == [])
    check("часы молчания переданы", out["silence_hours"] > 0, out)
    check("следующий ход считается дожимом",
          store_.data[555]["awaiting_followup"] is True)
    replies["state"] = "active"

    print("\n7. Отказы")
    class Denied(Exception):
        pass
    tenancy.Denied = tenancy.Denied

    def denied(u):
        raise tenancy.Denied("Исчерпан месячный лимит тренировок (200).")
    tenancy.check_can_train = denied
    try:
        await webroom.start(None, dict(USER))
        check("при исчерпанном лимите тренировка не начинается", False)
    except ValueError as e:
        check("при исчерпанном лимите тренировка не начинается", True)
        check("человеку объясняют причину", "лимит" in str(e).lower(), str(e))
    tenancy.check_can_train = lambda u: True

    niche_loader.active_profile = lambda cid: None
    try:
        await webroom.start(None, dict(USER))
        check("без профиля компании тренировка не начинается", False)
    except ValueError as e:
        check("без профиля компании тренировка не начинается", True)
        check("подсказано, что делать", "бриф" in str(e).lower(), str(e))
    niche_loader.active_profile = lambda cid: PROFILE

    store_.data.pop(555, None)
    try:
        await webroom.say(None, dict(USER), "Есть кто?")
        check("ход без тренировки отклонён", False)
    except ValueError:
        check("ход без тренировки отклонён", True)

    print("\n8. Сбой модели не роняет комнату")
    def broken(client, **kw):
        raise RuntimeError("модель недоступна")
    llm.create = broken
    engine.llm.create = broken
    r = await webroom.start(None, dict(USER))
    check("тренировка началась даже так", bool(r["client"]["name"]), r)
    check("клиент всё равно написал первым", len(r["transcript"]) >= 1, r["transcript"])

    print("\n9. Фото от менеджера")
    llm.create = fake_create
    engine.llm.create = fake_create
    seen_content = {}

    def capture(client, **kw):
        seen_content["content"] = kw["messages"][0]["content"]
        return fake_create(client, **kw)
    llm.create = capture
    engine.llm.create = capture

    await webroom.start(None, dict(USER))
    out = await webroom.say(None, dict(USER), "Вот наши работы",
                            "data:image/jpeg;base64,QUJD")
    blocks = seen_content["content"]
    check("картинка ушла в модель отдельным блоком",
          isinstance(blocks, list) and blocks[0]["type"] == "image", type(blocks))
    check("тип файла передан как есть",
          blocks[0]["source"]["media_type"] == "image/jpeg", blocks[0]["source"])
    check("фото помечено в переписке",
          out["transcript"][-2]["text"].startswith("📎"), out["transcript"][-2])
    check("сама картинка в память не легла",
          "QUJD" not in json.dumps(store_.data, ensure_ascii=False))

    out = await webroom.say(None, dict(USER), "", "data:image/png;base64,QUJD")
    check("фото без подписи проходит", out["turns"] >= 2, out["turns"])

    try:
        await webroom.say(None, dict(USER), "смотрите", "data:application/pdf;base64,QUJD")
        check("чужой тип файла отклонён", False)
    except ValueError as e:
        check("чужой тип файла отклонён", True)
        check("сказано, что подойдёт", "JPG" in str(e), str(e))

    await webroom.say(None, dict(USER), "Просто текст")
    check("без картинки запрос остаётся обычным текстом",
          isinstance(seen_content["content"], str), type(seen_content["content"]))

    print("\n" + "=" * 58)
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}: " + "; ".join(FAILS))
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    asyncio.run(main())
