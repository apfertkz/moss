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
        self.company = {"id": 1, "title": "Тест", "plan": "base", "status": "active",
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


class LegacyDB:
    """База, какой она была при сетке по местам: три тарифа и две компании."""

    def __init__(self):
        self.plans = [{"key": k} for k in ("start", "team", "dept")]
        self.prices = [{"plan_key": "dept", "months": 12, "price_kzt": 1790000}]
        self.companies = [{"id": 1, "plan": "dept", "seats": 40},
                          {"id": 2, "plan": "team", "seats": 15}]

    def query(self, sql, params=(), one=False):
        s = " ".join(sql.split())
        if "FROM plans WHERE key = ANY" in s:
            return [p for p in self.plans if p["key"] in params[0]]
        if "UPDATE companies SET plan='base'" in s:
            moved = [c for c in self.companies if c["plan"] in params[0]]
            for c in moved:
                c["plan"] = "base"
            return [{"id": c["id"]} for c in moved]
        return None if one else []

    def execute(self, sql, params=(), returning=False):
        s = " ".join(sql.split())
        if "INSERT INTO plans" in s:
            if not any(p["key"] == params[0] for p in self.plans):
                self.plans.append({"key": params[0]})
        elif "INSERT INTO plan_prices" in s:
            self.prices.append({"plan_key": params[0], "months": params[1],
                                "price_kzt": params[2]})
        elif "DELETE FROM plan_prices" in s:
            self.prices = [p for p in self.prices if p["plan_key"] not in params[0]]
        elif "DELETE FROM plans" in s:
            self.plans = [p for p in self.plans if p["key"] not in params[0]]
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
    check("месяц — 79 000", t.price_for("base", 1) == 79000, t.price_for("base", 1))
    check("три месяца — 210 000", t.price_for("base", 3) == 210000, t.price_for("base", 3))
    check("полгода — 400 000", t.price_for("base", 6) == 400000, t.price_for("base", 6))
    check("год — 700 000", t.price_for("base", 12) == 700000, t.price_for("base", 12))

    print("\n2. Скидка растёт со сроком")
    for plan, expect in (("base", 79000),):
        m1 = t.price_for(plan, 1)
        d3 = 1 - t.price_for(plan, 3) / (m1 * 3)
        d6 = 1 - t.price_for(plan, 6) / (m1 * 6)
        d12 = 1 - t.price_for(plan, 12) / (m1 * 12)
        check(f"{plan}: месяц как в тарифе", m1 == expect, m1)
        check(f"{plan}: 3 мес около −10%", 0.09 <= d3 <= 0.13, round(d3, 3))
        check(f"{plan}: 6 мес около −15%", 0.14 <= d6 <= 0.18, round(d6, 3))
        check(f"{plan}: год около −25%", 0.24 <= d12 <= 0.28, round(d12, 3))
        check(f"{plan}: чем дольше, тем выгоднее", d3 < d6 < d12)

    print("\n3. Неизвестный срок считается без скидки")
    check("два месяца — по месячной цене", t.price_for("base", 2) == 79000 * 2)

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
    t.record_payment(1, 3, 210000, "base", "перевод")
    t.record_payment(1, 12, 700000, "base")
    check("платежи записаны", len(fake.payments) == 2)
    check("сумма сохранена", fake.payments[0]["amount_kzt"] == 210000)
    check("срок сохранён", fake.payments[1]["months"] == 12)
    check("история отдаётся", len(t.payments_of(1)) == 2)

    print("\n6. Правка цены")
    t.save_price("base", 12, 950000)
    check("цена обновилась", t.price_for("base", 12) == 950000, t.price_for("base", 12))
    check("остальные не задеты", t.price_for("base", 3) == 210000)

    print("\n7. Уход от сетки по местам")
    legacy = LegacyDB()
    real_db.query = legacy.query
    real_db.execute = legacy.execute

    check("миграция сработала", t.retire_legacy_plans() is True)
    check("базовый тариф заведён", any(p["key"] == "base" for p in legacy.plans))
    check("старые тарифы убраны",
          not [p for p in legacy.plans if p["key"] in t.LEGACY_PLANS], legacy.plans)
    check("цены старых тарифов убраны",
          not [p for p in legacy.prices if p["plan_key"] in t.LEGACY_PLANS])
    check("компании переведены на базовый",
          all(c["plan"] == "base" for c in legacy.companies), legacy.companies)
    check("оплаченные места сохранены",
          [c["seats"] for c in legacy.companies] == [40, 15], legacy.companies)
    check("повторный запуск ничего не делает", t.retire_legacy_plans() is False)

    print("\n" + "=" * 58)
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}: " + "; ".join(FAILS))
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    main()
