# -*- coding: utf-8 -*-
"""
Проверки подписки: продление, смена тарифа, новый период, напоминания.

База подменяется заглушкой в памяти.

    python test_subscription.py
"""

import datetime
import sys

FAILS = []
UTC = datetime.timezone.utc


def check(name, cond, extra=""):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def now():
    return datetime.datetime.now(UTC)


class FakeDB:
    """Минимальная база: одна компания, один владелец, журнал и отметки."""

    def __init__(self):
        self.company = {
            "id": 1, "title": "Тест", "plan": "start", "seats": 5,
            "session_limit": 100, "sessions_used": 0,
            "period_started_at": now(), "expires_at": now() + datetime.timedelta(days=30),
            "status": "active", "activation_code": "AAA", "invite_code": "BBB",
        }
        self.users = [{"id": 1, "telegram_id": 111, "company_id": 1, "role": "owner",
                       "active": True}]
        self.log = []
        self.reminders = set()

    # --- чтение ---
    def query(self, sql, params=(), one=False):
        s = " ".join(sql.split())
        if "FROM users u JOIN companies c" in s:
            u = next((x for x in self.users if x["telegram_id"] == params[0]), None)
            if not u:
                return None
            row = {**u, "company_title": self.company["title"], "plan": self.company["plan"],
                   "company_status": self.company["status"], "seats": self.company["seats"],
                   "session_limit": self.company["session_limit"],
                   "sessions_used": self.company["sessions_used"],
                   "expires_at": self.company["expires_at"],
                   "period_started_at": self.company["period_started_at"],
                   "invite_code": self.company["invite_code"],
                   "activation_code": self.company["activation_code"]}
            return row if one else [row]
        if "FROM companies WHERE id" in s:
            return self.company if one else [self.company]
        if "FROM users WHERE company_id" in s and "role" in s:
            return self.users[0] if one else self.users
        if "FROM reminders_sent" in s:
            key = (params[0], params[1], params[2])
            return {"ok": 1} if key in self.reminders else None
        if "FROM companies WHERE status" in s:
            left = (self.company["expires_at"] - now()).days
            return [self.company] if 0 <= left <= int(params[1]) else []
        if "FROM admin_log" in s:
            return self.log
        return None if one else []

    # --- запись ---
    def execute(self, sql, params=(), returning=False):
        s = " ".join(sql.split())
        if "UPDATE companies SET sessions_used = 0, period_started_at = now()" in s:
            if self.company["period_started_at"] == params[1]:
                self.company["sessions_used"] = 0
                self.company["period_started_at"] = now()
            return None
        if "SET expires_at" in s:
            days = int(params[0])
            base = max(self.company["expires_at"] or now(), now())
            self.company["expires_at"] = base + datetime.timedelta(days=days)
            if self.company["status"] == params[1]:
                self.company["status"] = params[2]
            if params[3]:
                self.company["sessions_used"] = 0
                self.company["period_started_at"] = now()
            return self.company
        if "SET plan=" in s:
            self.company.update(plan=params[0], seats=params[1], session_limit=params[2])
            return self.company
        if "SET seats" in s:
            self.company["seats"] = max(1, self.company["seats"] + params[0])
            return self.company
        if "SET session_limit" in s:
            self.company["session_limit"] = max(0, self.company["session_limit"] + params[0])
            return self.company
        if "UPDATE companies SET status" in s:
            self.company["status"] = params[0]
            return self.company
        if "INSERT INTO admin_log" in s:
            self.log.append({"actor": params[0], "action": params[1], "company_id": params[2]})
            return None
        if "INSERT INTO reminders_sent" in s:
            self.reminders.add((params[0], params[1], params[2]))
            return None
        return None


def main():
    from trainer import db as real_db
    fake = FakeDB()
    real_db.query = fake.query
    real_db.execute = fake.execute

    from trainer import tenancy as t

    print("\n1. Новый период наступает сам")
    fake.company["sessions_used"] = 100
    u = t.get_user(111)
    check("пока период не вышел — лимит держится", t.roll_period_if_due(u)["sessions_used"] == 100)

    fake.company["period_started_at"] = now() - datetime.timedelta(days=31)
    u = t.get_user(111)
    rolled = t.roll_period_if_due(u)
    check("после 30 дней счётчик обнулился", rolled["sessions_used"] == 0, rolled["sessions_used"])

    print("\n2. Просроченной подписке период не дарим")
    fake.company["sessions_used"] = 100
    fake.company["period_started_at"] = now() - datetime.timedelta(days=40)
    fake.company["expires_at"] = now() - datetime.timedelta(days=5)
    u = t.get_user(111)
    check("срок истёк — обнуления нет", t.roll_period_if_due(u)["sessions_used"] == 100)

    print("\n3. Проверка доступа")
    try:
        t.check_can_train(t.get_user(111))
        check("истёкшая подписка не пускает", False)
    except t.Denied as d:
        check("истёкшая подписка не пускает", "истёк" in str(d))

    print("\n4. Продление")
    was = fake.company["expires_at"]
    c = t.extend(1, days=30)
    check("срок продлён от сегодня, а не от просроченной даты",
          (c["expires_at"] - now()).days >= 29, (c["expires_at"] - now()).days)
    check("продление не короче прежней даты", c["expires_at"] > was)
    check("счётчик тренировок обнулён", c["sessions_used"] == 0)
    check("после оплаты можно тренироваться", t.check_can_train(t.get_user(111)) is True)

    fake.company["status"] = t.STATUS_SUSPENDED
    c = t.extend(1)
    check("продление снимает приостановку", c["status"] == t.STATUS_ACTIVE)

    print("\n5. Тариф, места, пакеты")
    c = t.change_plan(1, "team")
    check("тариф сменился", c["plan"] == "team")
    check("места подтянулись", c["seats"] == t.PLANS["team"]["seats"])
    check("лимит подтянулся", c["session_limit"] == t.PLANS["team"]["session_limit"])
    try:
        t.change_plan(1, "неведомый")
        check("несуществующий тариф отвергается", False)
    except ValueError:
        check("несуществующий тариф отвергается", True)

    check("места добавляются", t.add_seats(1, 5)["seats"] == t.PLANS["team"]["seats"] + 5)
    check("места не уходят в минус", t.add_seats(1, -999)["seats"] == 1)
    base = fake.company["session_limit"]
    check("пакет тренировок добавляется", t.add_sessions(1, 50)["session_limit"] == base + 50)

    print("\n6. Сколько осталось")
    fake.company["expires_at"] = now() + datetime.timedelta(days=3, hours=1)
    check("дни считаются", t.days_left(fake.company) == 3, t.days_left(fake.company))
    fake.company["expires_at"] = now() - datetime.timedelta(days=1)
    check("истёкшая даёт ноль, а не минус", t.days_left(fake.company) == 0)

    print("\n7. Журнал")
    t.log_action("admin", "extend", company_id=1, details={"days": 30})
    check("запись появилась", len(fake.log) == 1)
    check("действие сохранено", fake.log[0]["action"] == "extend")

    print("\n8. Напоминания")
    import asyncio
    from trainer import notify

    fake.company["expires_at"] = now() + datetime.timedelta(days=2)
    fake.company["status"] = t.STATUS_ACTIVE
    sent = []

    class FakeBot:
        async def send_message(self, uid, text, **kw):
            sent.append((uid, text))

    bot = FakeBot()
    notify.ADMIN_IDS = set()
    n = asyncio.get_event_loop().run_until_complete(notify.run_reminders(bot))
    check("владельцу ушло напоминание", n == 1 and sent, f"n={n}")
    check("в тексте указан срок", sent and "заканчивается" in sent[0][1])

    sent.clear()
    n = asyncio.get_event_loop().run_until_complete(notify.run_reminders(bot))
    check("повторно то же самое не шлём", n == 0 and not sent)

    t.extend(1, days=30)
    fake.company["expires_at"] = now() + datetime.timedelta(days=2)
    n = asyncio.get_event_loop().run_until_complete(notify.run_reminders(bot))
    check("после продления круг начинается заново", n == 1)

    print("\n" + "=" * 58)
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}: " + "; ".join(FAILS))
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    main()
