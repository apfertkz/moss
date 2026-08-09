# -*- coding: utf-8 -*-
"""
Проверки мастера брифа. Модель подменяется заглушкой — проверяем логику
мастера и валидацию, а не качество текста.

    DATABASE_URL=... python test_brief.py
"""

import json
import sys
import types

from trainer import db, tenancy as t, brief, niche_loader

FAILS = []


def check(name, cond, extra=""):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def fake_client(payloads):
    """Клиент-заглушка: отдаёт заготовленные ответы по очереди."""
    seq = list(payloads)
    calls = []

    class Msgs:
        def create(self, **kw):
            calls.append(kw)
            body = seq.pop(0) if seq else "{}"
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(text=body)],
                usage=types.SimpleNamespace(input_tokens=1200, output_tokens=900,
                                            cache_creation_input_tokens=0,
                                            cache_read_input_tokens=0),
            )

    c = types.SimpleNamespace(messages=Msgs())
    c._calls = calls
    return c


def good_profile(n_status=9, n_req=12):
    return {
        "id": "moss_decor",
        "title": "MOSS — панно из стабилизированного мха",
        "product_context": ("Компания изготавливает на заказ декоративные панно из "
                            "стабилизированного мха для интерьеров. " * 6),
        "currency": "тенге",
        "statuses": [{"id": f"s{i}", "title": f"Клиент {i}",
                      "context": "Важна атмосфера и впечатление гостей."} for i in range(n_status)],
        "requests": [f"Запрос клиента номер {i}, довольно длинный" for i in range(n_req)],
    }


ANSWERS = {
    "product": "Изготавливаем панно из стабилизированного мха на заказ, не требует ухода, служит 8-10 лет",
    "audience": "Рестораны, кофейни, отели, дизайнеры интерьеров, владельцы квартир, офисы",
    "price": "Средний чек 350 тысяч тенге, от заявки до предоплаты одна-две недели",
    "channels": "Instagram Direct, WhatsApp, заявки с сайта",
    "first_messages": "Почём панно? / А примеры работ есть? / Нужно оформить ресепшн",
    "objections": "Дорого, надо подумать, а оно не завянет, посоветуюсь",
    "advantages": "Свой цех и монтажники, гарантия три года",
    "must_ask": "Площадь, место размещения, срок, кто принимает решение",
}


def fill(uid, answers=None):
    a = answers or ANSWERS
    for q in brief.QUESTIONS:
        brief.submit_answer(uid, a[q["key"]])


def main():
    with db.connection() as c:
        c.execute("DROP TABLE IF EXISTS usage_log, messages, sessions, "
                  "niche_profiles, users, companies CASCADE")
    db.init_db()
    company = t.create_company("Тест", plan="team")
    t.attach_user(1, company["id"], t.ROLE_OWNER, "Владелец")
    UID = 1

    print("\n1. Ход по вопросам")
    q = brief.start(UID, company["id"])
    check("мастер стартует с первого вопроса", q["key"] == "product")
    check("мастер активен", brief.is_active(UID))

    q2, err, done = brief.submit_answer(UID, "мох")
    check("короткий ответ отклонён", err is not None and not done)
    check("шаг не сдвинулся", brief.current_question(UID)["key"] == "product")

    q2, err, done = brief.submit_answer(UID, ANSWERS["product"])
    check("нормальный ответ принят", err is None and q2["key"] == "audience")

    back_q = brief.back(UID)
    check("«назад» возвращает на шаг", back_q["key"] == "product")
    brief.submit_answer(UID, ANSWERS["product"])

    print("\n2. Обязательные и необязательные вопросы")
    brief.submit_answer(UID, ANSWERS["audience"])
    brief.submit_answer(UID, ANSWERS["price"])
    q, err, done = brief.submit_answer(UID, "пропустить")   # channels — необязательный
    check("необязательный вопрос пропускается", err is None and q["key"] == "first_messages")
    q, err, done = brief.submit_answer(UID, "пропустить")   # first_messages — обязательный
    check("обязательный пропустить нельзя", err is not None)

    brief.cancel(UID)
    check("отмена очищает мастер", not brief.is_active(UID))

    print("\n3. Генерация профиля")
    brief.start(UID, company["id"])
    fill(UID)
    client = fake_client([json.dumps(good_profile(), ensure_ascii=False)])
    profile, err, usage = brief.generate(client, UID)
    check("профиль собран", profile is not None and err is None, err)
    check("расход посчитан", usage and usage["input_tokens"] > 0)
    check("в промпт попали ответы владельца",
          "стабилизированного мха" in client._calls[0]["messages"][0]["content"])

    print("\n4. Переспрашивание при плохом ответе модели")
    brief.start(UID, company["id"]); fill(UID)
    client = fake_client([
        "это вообще не json",
        json.dumps(good_profile(n_status=3), ensure_ascii=False),   # мало типов
        json.dumps(good_profile(n_req=4), ensure_ascii=False),      # мало запросов
    ])
    profile, err, _ = brief.generate(client, UID)
    check("после трёх неудач честно сдаётся", profile is None and err is not None)
    check("в тексте ошибки есть подсказка", err and "подробнее" in err)

    brief.start(UID, company["id"]); fill(UID)
    client = fake_client([
        json.dumps(good_profile(n_status=3), ensure_ascii=False),
        json.dumps(good_profile(), ensure_ascii=False),
    ])
    profile, err, _ = brief.generate(client, UID)
    check("со второй попытки получается", profile is not None and err is None)
    check("модели сообщили, что было не так",
          "минимум 8" in client._calls[1]["messages"][0]["content"])

    print("\n5. Сохранение и активация")
    version, err = brief.confirm(UID)
    check("профиль сохранён", version == 1 and err is None, err)
    check("мастер закрылся", not brief.is_active(UID))
    saved = niche_loader.active_profile(company["id"])
    check("профиль читается из базы", saved and saved["title"] == good_profile()["title"])

    row = db.query("SELECT brief FROM niche_profiles WHERE company_id=%s",
                   (company["id"],), one=True)
    check("ответы брифа сохранены рядом с профилем",
          row["brief"] and "стабилизированного мха" in row["brief"]["product"])

    t.set_status(company["id"], t.STATUS_ACTIVE)
    check("после настройки тренировка разрешена", t.check_can_train(t.get_user(1)))

    print("\n6. Повторная настройка версионируется")
    brief.start(UID, company["id"]); fill(UID)
    client = fake_client([json.dumps(dict(good_profile(), title="Вторая версия"), ensure_ascii=False)])
    brief.generate(client, UID)
    version2, err = brief.confirm(UID)
    check("версия выросла", version2 == 2, version2)
    check("действующий профиль — новый",
          niche_loader.active_profile(company["id"])["title"] == "Вторая версия")
    hist = niche_loader.profile_history(company["id"])
    check("старая версия осталась в истории", len(hist) == 2)
    active = db.query("SELECT COUNT(*) AS n FROM niche_profiles WHERE company_id=%s AND is_active",
                      (company["id"],), one=True)
    check("действующий профиль ровно один", active["n"] == 1)

    print("\n7. Замечание владельца уходит в модель")
    brief.start(UID, company["id"]); fill(UID)
    client = fake_client([json.dumps(good_profile(), ensure_ascii=False)])
    brief.generate(client, UID, remark="убери частных лиц, работаем только с бизнесом")
    check("замечание попало в промпт",
          "только с бизнесом" in client._calls[0]["messages"][0]["content"])
    brief.cancel(UID)

    test_regression()




def test_regression():
    """
    Отдельный прогон: сообщения, приходящие ПОСЛЕ последнего вопроса.
    Именно здесь бот замолкал целиком — QUESTIONS[8] бросал IndexError,
    хендлер падал, и Telegram не получал ничего.
    """
    print("\n8. Сообщения после последнего вопроса (регресс)")
    with db.connection() as c:
        c.execute("DELETE FROM niche_profiles; DELETE FROM users; DELETE FROM companies")
    company = t.create_company("Регресс", plan="team")
    t.attach_user(99, company["id"], t.ROLE_OWNER, "Владелец")
    UID = 99

    brief.start(UID, company["id"])
    fill(UID)
    check("после последнего ответа мастер ждёт подтверждения",
          brief.awaiting_confirmation(UID))

    for probe in ("👥 Отдел", "🎯 Тренажёр", "📊 Статистика", "что там", "назад"):
        try:
            q, err, done = brief.submit_answer(UID, probe)
            ok = True
        except Exception as e:
            ok = False
            print(f"      {type(e).__name__} на «{probe}»")
        check(f"не падает на «{probe}»", ok)

    client = fake_client([json.dumps(good_profile(), ensure_ascii=False)])
    check("во время сборки флаг не выставлен заранее", not brief.is_generating(UID))
    profile, err, _ = brief.generate(client, UID)
    check("профиль собрался", profile is not None, err)
    check("после сборки флаг снят", not brief.is_generating(UID))

    print("\n9. Модель вернула мусор неожиданной структуры")
    brief.start(UID, company["id"]); fill(UID)
    broken = good_profile()
    broken["statuses"] = ["просто строка", "и ещё одна", 42]
    client = fake_client([json.dumps(broken, ensure_ascii=False),
                          json.dumps(broken, ensure_ascii=False),
                          json.dumps(good_profile(), ensure_ascii=False)])
    try:
        profile, err, _ = brief.generate(client, UID)
        crashed = False
    except Exception as e:
        crashed = True
        print(f"      {type(e).__name__}: {e}")
    check("кривая структура не роняет мастер", crashed is False)
    check("с третьей попытки собирается", not crashed and profile is not None)
    check("флаг снят и после ошибок", not brief.is_generating(UID))
    brief.cancel(UID)

    print("\n" + "=" * 58)
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}: " + "; ".join(FAILS))
        sys.exit(1)
    print("Регрессионные проверки пройдены.")


if __name__ == "__main__":
    main()
