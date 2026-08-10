# -*- coding: utf-8 -*-
"""
Проверки сроков оплаты: цены, календарное продление, поступления, выручка.

    python test_terms.py
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
    def __init__(self):
        self.company = {"id": 1, "title": "Тест", "plan": "team", "status": "active",
                        "seats": 15, "session_limit": 300, "sessions_used": 120,
                        "period_started_at": now(), "expires_at": now() + datetime.timedelta(days=10)}
        self.prices = []
        self.payments = []

    def query(self, sql, params=(), one=False):
        s = " ".join(sql.split())
        if "FROM plan_prices" in s:
            return self.prices[0] if (one and self.prices) else self.prices
        if "FROM companies WHERE id" in s:
            return self.company if one else [self.company]
        if "FROM payments WHERE company_id" in s:
            return self.payments
        return None if one else []

    def execute(self, sql, params=(), returning=False):
        s = " ".join(sql.split())
        if "INSERT INTO plan_prices" in s and "ON CONFLICT DO NOTHING" in s:
            self.prices.append({"plan_key": params[0], "months": params[1], "price_kzt": params[2]})
            return None
        if "INSERT INTO plan_prices" in s:
            for r in self.prices:
                if r["plan_key"] == params[0] and r["months"] == params[1]:
                    r["price_kzt"] = params[2]
                    return None
            self.prices.append({"plan_key": params[0], "months": params[1], "price_kzt": params[2]})
            return None
        if "months')::interval" in s:
            months = int(params[0])
            base = max(self.company["expires_at"], now())
            # календарный месяц: приблизим 30.44 дня, точность здесь не важна
            self.company["expires_at"] = base + datetime.timedelta(days=round(30.44 * months))
            if params[3]:
                self.company["sessions_used"] = 0
                self.company["period_started_at"] = now()
            return self.company
        if "INSERT INTO payments" in s:
            row = {"id": len(self.payments) + 1, "company_id": params[0], "plan": params[1],
                   "months": params[2], "amount_kzt": params[3], "note": params[4],
                   "created_at": now()}
            self.payments.append(row)
            return row
        return None


def main():
    from trainer import db as real_db
    fake = FakeDB()
    real_db.query = fake.query
    real_db.execute = fake.execute

    from trainer import tenancy as t

    print("\n1. Цены засеваются и читаются")
    t.load_prices()
    check("цены появились", bool(t.PRICES), t.PRICES)
    check("три месяца «Команды» — 267 000", t.price_for("team", 3) == 267000, t.price_for("team", 3))
    check("год «Отдела» — 1 790 000", t.price_for("dept", 12) == 1790000, t.price_for("dept", 12))
    check("месяц «Старта» — 49 000", t.price_for("start", 1) == 49000)

    print("\n2. Скидка растёт со сроком")
    for plan, expect in (("start", 49000), ("team", 99000), ("dept", 199000)):
        m1 = t.price_for(plan, 1)
        d3 = 1 - t.price_for(plan, 3) / (m1 * 3)
        d6 = 1 - t.price_for(plan, 6) / (m1 * 6)
        d12 = 1 - t.price_for(plan, 12) / (m1 * 12)
        check(f"{plan}: 3 мес около −10%", 0.09 <= d3 <= 0.11, round(d3, 3))
        check(f"{plan}: 6 мес около −15%", 0.14 <= d6 <= 0.16, round(d6, 3))
        check(f"{plan}: год около −25%", 0.24 <= d12 <= 0.26, round(d12, 3))
        check(f"{plan}: чем дольше, тем выгоднее", d3 < d6 < d12)

    print("\n3. Неизвестный срок считается без скидки")
    check("два месяца — по месячной цене", t.price_for("team", 2) == 99000 * 2)

    print("\n4. Продление календарными месяцами")
    was = fake.company["expires_at"]
    c = t.extend_months(1, 3)
    added = (c["expires_at"] - was).days
    check("добавилось примерно три месяца", 88 <= added <= 95, added)
    check("счётчик тренировок обнулён", c["sessions_used"] == 0)

    fake.company["expires_at"] = now() - datetime.timedelta(days=20)
    c = t.extend_months(1, 12)
    left = (c["expires_at"] - now()).days
    check("просроченной считаем от сегодня, а не от старой даты", left >= 360, left)

    print("\n5. Поступления")
    t.record_payment(1, 3, 267000, "team", "перевод")
    t.record_payment(1, 12, 890000, "team")
    check("платежи записаны", len(fake.payments) == 2)
    check("сумма сохранена", fake.payments[0]["amount_kzt"] == 267000)
    check("срок сохранён", fake.payments[1]["months"] == 12)
    check("история отдаётся", len(t.payments_of(1)) == 2)

    print("\n6. Правка цены")
    t.save_price("team", 12, 950000)
    check("цена обновилась", t.price_for("team", 12) == 950000, t.price_for("team", 12))
    check("остальные не задеты", t.price_for("team", 3) == 267000)

    print("\n" + "=" * 58)
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}: " + "; ".join(FAILS))
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    main()
