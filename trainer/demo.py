# -*- coding: utf-8 -*-
"""
Демо-режим: дать человеку с сайта попробовать тренажёр без кода и без оплаты.

Зачем отдельный модуль. Раньше незнакомец, пришедший по кнопке «Запустить
пилот», упирался в стену «доступ выдаёт компания» — самый горячий момент
воронки тратился впустую. Теперь он получает настоящую тренировку: случайный
клиент, живой диалог, разбор в конце. И только после этого — предложение
подключить свой отдел.

Как устроено. Демо — это обычная компания в базе, просто служебная: у неё
фиксированный код, всегда активный статус и большой лимит. Гость становится
её менеджером. Это позволяет переиспользовать весь механизм тренировки,
статистики и учёта расхода, не плодя параллельных веток в коде.

Ограничение — на человека, а не на компанию: считаем завершённые тренировки
этого telegram_id. Иначе один любопытный сжёг бы лимит для всех.
"""

import logging

from . import db, tenancy, niche_loader

log = logging.getLogger(__name__)

TITLE = "Демо"
ACTIVATION_CODE = "DEMO0000"
INVITE_CODE = "DEMOJOIN"

# Сколько тренировок отдаём бесплатно. Одной мало — человек не успевает
# понять механику; трёх много — пропадает повод оставить заявку.
LIMIT = 2

_cached_id = None


def company():
    """Служебная компания демо. Создаётся один раз, дальше берётся из базы."""
    global _cached_id

    row = db.query("SELECT * FROM companies WHERE activation_code=%s",
                   (ACTIVATION_CODE,), one=True)
    if not row:
        row = db.execute(
            """INSERT INTO companies
                 (title, plan, activation_code, invite_code, seats, session_limit,
                  status, expires_at)
               VALUES (%s,'trial',%s,%s,100000,1000000,'active', now() + interval '100 years')
               RETURNING *""",
            (TITLE, ACTIVATION_CODE, INVITE_CODE), returning=True,
        )
        log.info("Создана демо-компания id=%s", row["id"])

    _cached_id = row["id"]
    _ensure_profile(row["id"])
    return row


def _ensure_profile(company_id):
    """У демо-компании должен быть профиль ниши, иначе тренировка не стартует."""
    if niche_loader.active_profile(company_id):
        return
    profile = niche_loader.load_file_profile("demo")
    niche_loader.save_profile(company_id, profile)
    log.info("Демо-компании проставлен профиль ниши «%s»", profile.get("title"))


def company_id():
    if _cached_id is not None:
        return _cached_id
    return company()["id"]


def is_demo(user):
    """Пользователь сидит в демо, а не в настоящей компании."""
    if not user:
        return False
    return user["company_id"] == company_id()


def join(telegram_id, full_name=None, username=None):
    """Пустить гостя в демо. Возвращает пользователя."""
    c = company()
    user, err = tenancy.attach_user(
        telegram_id, c["id"], tenancy.ROLE_MANAGER, full_name, username)
    if err:
        # Аккаунт уже принадлежит настоящей компании — демо ему не нужно.
        return None
    return user


def used(telegram_id):
    """Сколько демо-тренировок человек уже завершил."""
    row = db.query(
        "SELECT COUNT(*) AS n FROM sessions WHERE telegram_id=%s AND company_id=%s",
        (telegram_id, company_id()), one=True,
    )
    return int(row["n"]) if row else 0


def left(telegram_id):
    return max(0, LIMIT - used(telegram_id))


def release(telegram_id):
    """
    Выпустить человека из демо, чтобы он мог войти в настоящую компанию.

    Вызывается перед активацией по коду: без этого привязка упрётся в
    «аккаунт уже привязан к другой компании». Демо-тренировки при этом
    удаляются вместе с пользователем — их незачем хранить.
    """
    user = tenancy.get_user(telegram_id)
    if not user or user["company_id"] != company_id():
        return False
    db.execute("DELETE FROM users WHERE telegram_id=%s", (telegram_id,))
    log.info("Пользователь %s выпущен из демо", telegram_id)
    return True


# --- Тексты -----------------------------------------------------------------

INTRO = (
    "Это aisaty — тренажёр отдела продаж.\n\n"
    "Можно попробовать прямо сейчас, без регистрации. "
    f"Дам {LIMIT} тренировки на демо-нише: поставка оборудования для бизнеса.\n\n"
    "Выпадет случайный клиент со скрытым характером. Он напишет первым, "
    "будет торговаться и уйдёт, если отработаете плохо. В конце — разбор, "
    "где именно вы его потеряли."
)

START = (
    "Демо-режим. Ниша условная — оборудование и сервис для бизнеса.\n\n"
    "Под ваш продукт профиль собирается отдельно: бот будет играть именно "
    "ваших клиентов с их возражениями и ценами."
)


def tail(telegram_id):
    """Что сказать после демо-тренировки."""
    remaining = left(telegram_id)
    if remaining > 0:
        return (
            f"Осталось демо-тренировок: {remaining}.\n"
            "Жмите «🎯 Новый клиент» — выпадет другой клиент с другим характером."
        )
    return (
        "Демо закончилось.\n\n"
        "Дальше — ваш отдел и ваша ниша: профиль клиентов собирается из вашего "
        "брифа, у каждого менеджера своя статистика и конверсия по типам клиентов.\n\n"
        "Напишите нам, и запустим пилот на вашем отделе."
    )
