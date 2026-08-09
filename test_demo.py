# -*- coding: utf-8 -*-
"""
Проверки демо-режима: служебная компания, лимит на человека, выход в
настоящую компанию.

База подменяется заглушкой в памяти — проверяем логику, а не Postgres.

    python test_demo.py
"""

import sys
import types

FAILS = []


def check(name, cond, extra=""):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


# --- Заглушка базы ----------------------------------------------------------

class FakeDB:
    def __init__(self):
        self.companies = []
        self.users = []
        self.sessions = []
        self.profiles = []
        self._seq = 0

    def _next(self):
        self._seq += 1
        return self._seq

    def query(self, sql, params=(), one=False):
        s = " ".join(sql.split())
        if "FROM companies WHERE activation_code" in s:
            rows = [c for c in self.companies if c["activation_code"] == params[0]]
        elif "FROM companies WHERE id" in s:
            rows = [c for c in self.companies if c["id"] == params[0]]
        elif "COUNT(*) AS n FROM sessions" in s:
            n = len([x for x in self.sessions
                     if x["telegram_id"] == params[0] and x["company_id"] == params[1]])
            return {"n": n}
        elif "FROM users u JOIN companies c" in s:
            rows = []
            for u in self.users:
                if u["telegram_id"] != params[0]:
                    continue
                c = next(c for c in self.companies if c["id"] == u["company_id"])
                rows.append({**u, "company_title": c["title"], "plan": c["plan"],
                             "company_status": c["status"], "seats": c["seats"],
                             "session_limit": c["session_limit"],
                             "sessions_used": c["sessions_used"],
                             "expires_at": c["expires_at"],
                             "invite_code": c["invite_code"],
                             "activation_code": c["activation_code"]})
        elif "COUNT(*) AS n FROM users" in s:
            return {"n": len([u for u in self.users if u["company_id"] == params[0]])}
        elif "FROM niche_profiles" in s:
            rows = [p for p in self.profiles if p["company_id"] == params[0] and p["is_active"]]
        else:
            rows = []
        return (rows[0] if rows else None) if one else rows

    def execute(self, sql, params=(), returning=False):
        s = " ".join(sql.split())
        if "INSERT INTO companies" in s:
            row = {"id": self._next(), "title": params[0], "plan": "trial",
                   "activation_code": params[1], "invite_code": params[2],
                   "seats": 100000, "session_limit": 1000000, "sessions_used": 0,
                   "status": "active", "expires_at": None}
            self.companies.append(row)
            return row
        if "INSERT INTO users" in s:
            self.users.append({"id": self._next(), "telegram_id": params[0],
                               "company_id": params[1], "role": params[2],
                               "full_name": params[3], "username": params[4],
                               "active": True})
            return None
        if "DELETE FROM users" in s:
            before = len(self.users)
            self.users = [u for u in self.users if u["telegram_id"] != params[0]]
            self.sessions = [x for x in self.sessions if x["telegram_id"] != params[0]]
            return before != len(self.users)
        if "INSERT INTO niche_profiles" in s:
            self.profiles.append({"id": self._next(), "company_id": params[0],
                                  "profile": params[1], "is_active": True})
            return {"id": self._seq}
        if "UPDATE niche_profiles" in s:
            return None
        return None


def main():
    from trainer import db as real_db
    fake = FakeDB()
    real_db.query = fake.query
    real_db.execute = fake.execute

    from trainer import demo, tenancy, niche_loader

    # save_profile ходит в базу двумя запросами — упрощаем
    niche_loader.save_profile = lambda cid, profile, brief=None: fake.execute(
        "INSERT INTO niche_profiles", (cid, profile))
    niche_loader.active_profile = lambda cid: next(
        (p["profile"] for p in fake.profiles if p["company_id"] == cid), None)

    print("\n1. Служебная компания")
    c1 = demo.company()
    c2 = demo.company()
    check("создаётся один раз", c1["id"] == c2["id"] and len(fake.companies) == 1)
    check("статус активный", c1["status"] == "active")
    check("лимит не мешает демо", c1["session_limit"] >= 100000)
    check("профиль ниши проставлен", niche_loader.active_profile(c1["id"]) is not None)

    print("\n2. Вход гостя")
    u = demo.join(555, "Гость", "guest")
    check("гость привязан", u is not None and u["company_id"] == c1["id"])
    check("распознаётся как демо", demo.is_demo(u))
    check("повторный вход не ломается", demo.join(555) is not None)

    print("\n3. Лимит на человека, а не на компанию")
    check(f"вначале доступно {demo.LIMIT}", demo.left(555) == demo.LIMIT)
    fake.sessions.append({"telegram_id": 555, "company_id": c1["id"]})
    check("после одной тренировки на одну меньше", demo.left(555) == demo.LIMIT - 1)
    for _ in range(demo.LIMIT):
        fake.sessions.append({"telegram_id": 555, "company_id": c1["id"]})
    check("лимит не уходит в минус", demo.left(555) == 0)

    other = demo.join(777, "Второй")
    check("у другого гостя свой счёт", demo.left(777) == demo.LIMIT)

    print("\n4. Тексты")
    check("пока есть попытки — зовём продолжить", "Новый клиент" in demo.tail(777))
    check("когда кончились — зовём на пилот", "пилот" in demo.tail(555).lower())
    check("во вступлении сказано, что без регистрации", "без регистрации" in demo.INTRO)

    print("\n5. Выход в настоящую компанию")
    real = fake.execute("INSERT INTO companies", ("Клиент", "AAA11111", "BBB22222"),
                        returning=True)
    check("из демо выпускает", demo.release(555) is True)
    check("после выхода записи нет", tenancy.get_user(555) is None)
    u2, err = tenancy.attach_user(555, real["id"], tenancy.ROLE_MANAGER)
    check("теперь можно войти в настоящую компанию", err is None and u2 is not None)
    check("и это уже не демо", not demo.is_demo(u2))
    check("посторонних не трогает", demo.release(999) is False)

    print("\n" + "=" * 58)
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}: " + "; ".join(FAILS))
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    main()
