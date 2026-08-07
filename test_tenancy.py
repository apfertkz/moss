# -*- coding: utf-8 -*-
"""
Проверки мультиарендности. Запуск на чистой базе:
    DATABASE_URL=... python test_tenancy.py

Проверяем то, что в ТЗ записано как критерии приёмки:
изоляция компаний, лимиты, места, активация по ссылке, сохранность данных.
"""

import sys
from trainer import db, tenancy as t, niche_loader, stats, onboarding, engine

FAILS = []


def check(name, cond, extra=""):
    mark = "ok  " if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def reset():
    with db.connection() as c:
        c.execute("DROP TABLE IF EXISTS usage_log, messages, sessions, "
                  "niche_profiles, users, companies CASCADE")
    db.init_db()


def main():
    reset()
    print("\n1. Создание компаний и активация по ссылке")
    a = t.create_company("MOSS Алматы", plan="team")
    b = t.create_company("Кофейня Север", plan="start")
    check("коды активации уникальны", a["activation_code"] != b["activation_code"])
    check("коды приглашения уникальны", a["invite_code"] != b["invite_code"])

    user, text, ok = onboarding.redeem(a["activation_code"], 1001, "Александр")
    check("владелец активировал компанию по коду", ok and user["role"] == t.ROLE_OWNER)
    check("владельцу предложен бриф", "setup" in (text or ""))

    _, text2, ok2 = onboarding.redeem(a["activation_code"], 2002, "Чужой")
    check("повторная активация чужим отклонена", not ok2, text2)

    _, _, ok3 = onboarding.redeem("НЕТТАКОГО", 3003)
    check("неверный код отклонён", not ok3)

    m, textm, okm = onboarding.redeem(f"join_{a['invite_code']}", 1002, "Менеджер1")
    check("менеджер вошёл по ссылке-приглашению", okm and m["role"] == t.ROLE_MANAGER)
    check("менеджер попал в правильную компанию", m["company_id"] == a["id"])

    print("\n2. Лимит мест по тарифу")
    small = t.create_company("Маленькая", plan="start")   # 5 мест
    onboarding.redeem(small["activation_code"], 5000, "Владелец")
    added, denied = 0, None
    for i in range(10):
        u, err = t.attach_user(5100 + i, small["id"], t.ROLE_MANAGER, f"М{i}")
        if err:
            denied = err
            break
        added += 1
    check("места ограничены тарифом", added == 4, f"добавлено {added}, ожидалось 4")
    check("при переполнении понятная ошибка", denied and "мест" in denied, denied)

    print("\n3. Изоляция данных между компаниями")
    onboarding.redeem(b["activation_code"], 2001, "Владелец Б")
    prof = niche_loader.load_file_profile("moss")
    niche_loader.save_profile(a["id"], prof)
    niche_loader.save_profile(b["id"], dict(prof, title="Кофейня — профиль"))
    t.set_status(a["id"], t.STATUS_ACTIVE)
    t.set_status(b["id"], t.STATUS_ACTIVE)

    pa = niche_loader.active_profile(a["id"])
    pb = niche_loader.active_profile(b["id"])
    check("у каждой компании свой профиль", pa["title"] != pb["title"])

    ua, ub = t.get_user(1002), t.get_user(2001)
    sc = engine.new_scenario(pa)
    for _ in range(3):
        stats.record_session(ua, sc, "won", 7, [("manager", "привет"), ("buyer", "почём?")])
    stats.record_session(ub, sc, "failed", 4)

    rows_a = db.query("SELECT COUNT(*) AS n FROM sessions WHERE company_id=%s", (a["id"],), one=True)
    rows_b = db.query("SELECT COUNT(*) AS n FROM sessions WHERE company_id=%s", (b["id"],), one=True)
    check("тренировки компании А видит только А", rows_a["n"] == 3, rows_a["n"])
    check("тренировки компании Б видит только Б", rows_b["n"] == 1, rows_b["n"])

    rep_a = stats.report_for_company(a["id"])
    check("в сводке А нет сотрудников Б", "Владелец Б" not in rep_a)
    msgs = db.query("SELECT COUNT(*) AS n FROM messages WHERE company_id=%s", (b["id"],), one=True)
    check("реплики привязаны к своей компании", msgs["n"] == 0)

    print("\n4. Лимит тренировок")
    db.execute("UPDATE companies SET session_limit=5, sessions_used=0 WHERE id=%s", (a["id"],))
    for i in range(5):
        t.consume_session(a["id"])
    u = t.get_user(1002)
    denied_msg = None
    try:
        t.check_can_train(u)
    except t.Denied as d:
        denied_msg = str(d)
    check("при исчерпании лимита тренировка запрещена", denied_msg is not None)
    check("текст отказа объясняет причину", denied_msg and "лимит" in denied_msg.lower())

    t.reset_period(a["id"])
    check("сброс периода обнуляет счётчик", t.get_user(1002)["sessions_used"] == 0)
    check("после сброса тренировка снова разрешена", t.check_can_train(t.get_user(1002)))

    print("\n5. Предупреждение на 80% лимита")
    db.execute("UPDATE companies SET session_limit=10, sessions_used=7 WHERE id=%s", (a["id"],))
    used, limit = t.consume_session(a["id"])
    check("предупреждение приходит ровно на 80%", t.usage_warning(used, limit) is not None)
    used, limit = t.consume_session(a["id"])
    check("и не повторяется на следующей", t.usage_warning(used, limit) is None)

    print("\n6. Отключённый сотрудник и приостановка подписки")
    t.set_user_active(a["id"], 1002, False)
    try:
        t.check_can_train(t.get_user(1002)); off = False
    except t.Denied:
        off = True
    check("отключённый сотрудник не допущен", off)
    t.set_user_active(a["id"], 1002, True)

    t.set_status(a["id"], t.STATUS_SUSPENDED)
    try:
        t.check_can_train(t.get_user(1002)); susp = False
    except t.Denied:
        susp = True
    check("при приостановке подписки доступ закрыт", susp)
    t.set_status(a["id"], t.STATUS_ACTIVE)

    print("\n7. Отзыв ссылки-приглашения")
    old = t.get_company(a["id"])["invite_code"]
    new = t.rotate_invite_code(a["id"])
    check("новый код отличается", old != new)
    _, _, ok_old = onboarding.redeem(f"join_{old}", 7777)
    check("старая ссылка перестала работать", not ok_old)
    _, _, ok_new = onboarding.redeem(f"join_{new}", 7778, "Новичок")
    check("новая ссылка работает", ok_new)

    print("\n8. Учёт расхода и себестоимость")
    from trainer import costs
    usage = {"input_tokens": 8580, "output_tokens": 1320, "cache_write": 3500, "cache_read": 38500}
    cost = costs.cost_usd("claude-sonnet-5", usage)
    check("стоимость диалога считается", 0.05 < cost < 0.09, f"${cost:.4f}")
    stats.record_usage(a["id"], 1002, "step", "claude-sonnet-5", usage)
    spend = stats.company_spend(a["id"])
    check("расход записан в журнал", float(spend["usd"]) > 0)
    no_cache = costs.cost_usd("claude-sonnet-5",
                              {"input_tokens": 53180, "output_tokens": 2020,
                               "cache_write": 0, "cache_read": 0})
    check("кеширование дешевле, чем без него", cost < no_cache,
          f"{cost:.4f} против {no_cache:.4f}")

    print("\n9. Валидация профиля ниши")
    bad_cases = [
        ({}, "пустой профиль"),
        (dict(prof, statuses=[]), "без типов клиентов"),
        (dict(prof, requests=["ок"]), "слишком мало запросов"),
        (dict(prof, product_context="мало"), "куцое описание продукта"),
    ]
    for bad, name in bad_cases:
        try:
            niche_loader.validate(dict(bad))
            check(f"отклоняет: {name}", False)
        except niche_loader.InvalidProfile:
            check(f"отклоняет: {name}", True)
    check("принимает корректный профиль", niche_loader.validate(dict(prof)) is not None)

    print("\n10. Сохранность данных при перезапуске")
    db._pool = None   # имитируем рестарт процесса
    again = t.get_user(1002)
    check("пользователь на месте после переподключения", again is not None)
    check("статистика на месте", db.query(
        "SELECT COUNT(*) AS n FROM sessions WHERE company_id=%s", (a["id"],), one=True)["n"] == 3)

    print("\n" + "=" * 58)
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}: " + "; ".join(FAILS))
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    main()
