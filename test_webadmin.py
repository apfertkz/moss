# -*- coding: utf-8 -*-
"""
Проверки веб-панели: вход, защита маршрутов, действия над клиентом.

Поднимаем настоящее приложение aiohttp с заглушкой вместо базы и бота.

    python test_webadmin.py
"""

import asyncio
import sys
import types

FAILS = []


def check(name, cond, extra=""):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, uid, text, **kw):
        self.sent.append((uid, text))

    async def get_me(self):
        return types.SimpleNamespace(username="moss_test_bot")


async def main():
    import os
    os.environ["ADMIN_PASSWORD"] = "secret123"

    from trainer import webadmin, admin_data, tenancy, notify, db

    webadmin.PASSWORD = "secret123"
    webadmin.SECRET = "test-secret"
    notify.ADMIN_IDS = {999}

    # ——— заглушки данных ———
    company = {"id": 1, "title": "Тест", "plan": "start", "status": "active"}
    admin_data.overview = lambda: {"companies": {"active": 1}, "revenue_kzt": 49000}
    admin_data.companies = lambda status=None, q=None, limit=200: [dict(company)]
    admin_data.company = lambda cid: dict(company) if cid == 1 else None
    admin_data.users = lambda q=None, company_id=None, limit=200: []
    admin_data.demo_queue = lambda: []
    admin_data.segment = lambda name, company_id=None: [111, 222, 333]
    admin_data.SEGMENTS = {"owners": "Владельцы"}
    extended = []
    tenancy.extend = lambda cid, days=30, reset_usage=True: extended.append((cid, days)) or dict(company)
    tenancy.log_action = lambda *a, **k: None
    db.healthcheck = lambda: True

    bot = FakeBot()
    app = webadmin.build_app(bot)

    from aiohttp.test_utils import TestServer, TestClient
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    print("\n1. Закрытые маршруты")
    r = await client.get("/api/overview")
    check("без входа обзор не отдаётся", r.status == 401, r.status)
    r = await client.get("/api/companies")
    check("без входа список клиентов не отдаётся", r.status == 401, r.status)
    r = await client.get("/api/health")
    check("проверка здоровья открыта", r.status == 200)

    print("\n2. Вход")
    r = await client.post("/api/login", json={"password": "мимо"})
    check("неверный пароль отвергается", r.status == 403)
    check("код при этом не отправлен", not bot.sent)

    r = await client.post("/api/login", json={"password": "secret123"})
    data = await r.json()
    check("верный пароль принят", r.status == 200 and "token" in data)
    check("код ушёл в Telegram", len(bot.sent) == 1 and "999" == str(bot.sent[0][0]))

    code = "".join(ch for ch in bot.sent[0][1] if ch.isdigit())[-6:]
    r = await client.post("/api/verify", json={"token": data["token"], "code": "000000"})
    check("неверный код отвергается", r.status == 403)

    r = await client.post("/api/verify", json={"token": data["token"], "code": code})
    check("верный код пускает", r.status == 200, r.status)

    print("\n3. После входа")
    r = await client.get("/api/overview")
    check("обзор отдаётся", r.status == 200)
    check("в обзоре есть выручка", (await r.json()).get("revenue_kzt") == 49000)

    r = await client.get("/api/companies")
    check("список клиентов отдаётся", r.status == 200 and len(await r.json()) == 1)

    r = await client.get("/api/companies/1")
    check("карточка отдаётся", r.status == 200)
    r = await client.get("/api/companies/777")
    check("несуществующая — 404", r.status == 404)

    print("\n4. Действия над клиентом")
    r = await client.post("/api/companies/1/extend", json={"days": 90})
    check("продление проходит", r.status == 200)
    check("продлено на переданный срок", extended == [(1, 90)], extended)

    r = await client.post("/api/companies/1/неведомое", json={})
    check("неизвестное действие отвергается", r.status == 400)

    print("\n5. Рассылка")
    r = await client.post("/api/broadcast/preview", json={"segment": "owners"})
    check("предпросмотр считает получателей", (await r.json())["count"] == 3)

    r = await client.post("/api/broadcast/send", json={"text": ""})
    check("пустой текст не уходит", r.status == 400)

    bot.sent.clear()
    r = await client.post("/api/broadcast/send", json={"segment": "owners", "text": "Привет"})
    res = await r.json()
    check("рассылка ушла всем", res["sent"] == 3, res)

    bot.sent.clear()
    r = await client.post("/api/broadcast/send", json={"text": "Тест", "test": True})
    check("тестовая отправка идёт только администратору",
          (await r.json())["sent"] == 1 and bot.sent[0][0] == 999)

    print("\n6. Выход")
    r = await client.post("/api/logout")
    check("выход принят", r.status == 200)

    print("\n7. Подпись сессии")
    t = webadmin.make_token()
    check("свой токен принимается", webadmin.valid_token(t))
    check("подделанный отвергается", not webadmin.valid_token(t[:-3] + "aaa"))
    check("мусор отвергается", not webadmin.valid_token("что-то"))

    t = webadmin.make_token(webadmin.owner_subject(5, 777, False))
    check("токен руководителя разбирается", webadmin.valid_token(t))

    print("\n8. Кабинет руководителя")
    from trainer import accounts

    stored = accounts.hash_password("pravilny-parol")
    accounts.check = lambda tg, pw: (
        {"telegram_id": 777, "company_id": 5, "must_change": True}
        if tg == 777 and accounts.verify(pw, stored) else None)
    accounts.set_password = lambda tg, pw: None
    accounts.panel_url = lambda: "https://panel.example"
    admin_data.company = lambda cid: {"id": cid, "title": f"К{cid}", "plan": "start",
                                      "plan_title": "Старт", "status": "active",
                                      "seats": 5, "seats_taken": 2, "session_limit": 100,
                                      "sessions_used": 7, "expires_at": None,
                                      "days_left": None, "invite_code": "AAA"}
    admin_data.company_summary = lambda cid, days=30: {"company_id": cid, "sessions": 3}

    r = await client.post("/api/login", json={"login": "777", "password": "мимо"})
    check("чужой пароль руководителя не проходит", r.status == 403, r.status)

    r = await client.post("/api/login", json={"login": "777", "password": "pravilny-parol"})
    body = await r.json()
    check("руководитель входит одной ступенью", r.status == 200 and body["role"] == "owner")
    check("панель просит сменить пароль", body["must_change"] is True)

    r = await client.get("/api/my/summary")
    check("до смены пароля кабинет закрыт", r.status == 403, r.status)
    r = await client.get("/api/my/me")
    check("но своя карточка отдаётся", r.status == 200, r.status)

    bot.sent.clear()
    r = await client.post("/api/my/password", json={"password": "korotkiy", "repeat": "другой"})
    check("несовпадающие пароли отвергаются", r.status == 400)
    r = await client.post("/api/my/password", json={"password": "1234567", "repeat": "1234567"})
    check("короткий пароль отвергается", r.status == 400)

    r = await client.post("/api/my/password",
                          json={"password": "novyj-parol-77", "repeat": "novyj-parol-77"})
    check("новый пароль принят", r.status == 200, r.status)
    check("подтверждение ушло в Telegram", len(bot.sent) == 1 and bot.sent[0][0] == 777)
    check("сам пароль в сообщение не попал", "novyj-parol-77" not in bot.sent[0][1])

    r = await client.get("/api/my/summary")
    check("после смены пароля кабинет открыт", r.status == 200, r.status)
    check("сводка ограничена своей компанией",
          (await r.json()).get("company_id") == 5)

    print("\n9. Границы прав руководителя")
    for path in ("/api/overview", "/api/companies", "/api/money", "/api/settings",
                 "/api/log", "/api/users", "/api/export/companies"):
        r = await client.get(path)
        check(f"{path} закрыт для руководителя", r.status == 403, r.status)
    r = await client.post("/api/broadcast/send", json={"text": "чужим"})
    check("общая рассылка закрыта для руководителя", r.status == 403, r.status)

    # Подмена номера компании в запросе не должна ничего менять.
    r = await client.get("/api/my/summary?company_id=1&days=30")
    check("company_id из запроса игнорируется", (await r.json()).get("company_id") == 5)

    seen = {}
    admin_data.segment = lambda name, company_id=None: (
        seen.update({"cid": company_id}) or [111, 222])
    r = await client.post("/api/my/broadcast/preview", json={"segment": "all"})
    check("рассылка руководителя считается по его компании", seen.get("cid") == 5, seen)

    await client.post("/api/logout")
    r = await client.get("/api/my/me")
    check("после выхода кабинет закрыт", r.status == 401, r.status)

    await client.close()

    print("\n" + "=" * 58)
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}: " + "; ".join(FAILS))
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    asyncio.run(main())
