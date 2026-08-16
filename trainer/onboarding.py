# -*- coding: utf-8 -*-
"""
Вход в систему по ссылке (deep-link).

Telegram передаёт в /start произвольную нагрузку: t.me/BOT?start=<payload>.
На этом строится вся выдача доступов — клиенту не нужно ничего вводить руками.

Форматы нагрузки:
  <ACTIVATION_CODE>        — активация владельцем после оплаты (код из письма)
  join_<INVITE_CODE>       — переход менеджера по ссылке от руководителя

Ошибочный или устаревший код не создаёт пользователя и показывает понятный текст.
"""

import logging

from . import tenancy, demo

log = logging.getLogger(__name__)

JOIN_PREFIX = "join_"


def parse_payload(payload):
    """Вернуть ('owner'|'manager'|None, код)."""
    if not payload:
        return None, None
    payload = payload.strip()
    if payload.lower().startswith(JOIN_PREFIX):
        return "manager", payload[len(JOIN_PREFIX):].upper()
    return "owner", payload.upper()


def redeem(payload, telegram_id, full_name=None, username=None):
    """
    Обработать переход по ссылке. Возвращает (user, текст_ответа, ok).
    Ничего не создаёт, если код неверный.
    """
    kind, code = parse_payload(payload)
    if not kind:
        return None, None, False

    # Гость мог до этого гонять демо. Пока он числится менеджером служебной
    # компании, привязать его к настоящей нельзя — сначала выпускаем.
    try:
        demo.release(telegram_id)
    except Exception:
        log.exception("Не удалось выпустить пользователя из демо")

    if kind == "manager":
        company = tenancy.company_by_invite_code(code)
        if not company:
            return None, ("Ссылка недействительна или отозвана.\n"
                          "Попросите руководителя прислать актуальную — команда /invite."), False

        user, err = tenancy.attach_user(
            telegram_id, company["id"], tenancy.ROLE_MANAGER, full_name, username)
        if err:
            return None, err, False

        return user, (
            f"Готово — вы в отделе продаж компании *{company['title']}*.\n\n"
            f"🎯 Жмите «Тренажёр» внизу: вам выпадет клиент со скрытым характером и запросом. "
            f"Он напишет первым. Ваша задача — довести его до сделки.\n\n"
            f"После каждой тренировки — разбор, где именно вы его потеряли."
        ), True

    company = tenancy.company_by_activation_code(code)
    if not company:
        return None, ("Код активации не найден. Проверьте ссылку из письма "
                      "или напишите в поддержку."), False

    existing_owner = tenancy.db.query(
        "SELECT 1 FROM users WHERE company_id=%s AND role=%s",
        (company["id"], tenancy.ROLE_OWNER), one=True,
    )
    if existing_owner:
        user = tenancy.get_user(telegram_id)
        if user and user["company_id"] == company["id"]:
            return user, f"С возвращением, *{company['title']}*.", True
        return None, ("Эта компания уже активирована другим аккаунтом. "
                      "Если это ошибка — напишите в поддержку."), False

    # Владельца в компании ещё нет. Если этот человек уже привязан к ней
    # менеджером — повышаем его, а не отдаём запись как есть: иначе роль
    # осталась бы менеджерской, и компания так и стояла бы без руководителя.
    existing = tenancy.get_user(telegram_id)
    if existing and existing["company_id"] == company["id"]:
        user = tenancy.promote_to_owner(telegram_id, company["id"])
    else:
        user, err = tenancy.attach_user(
            telegram_id, company["id"], tenancy.ROLE_OWNER, full_name, username)
        if err:
            return None, err, False

    plan = tenancy.PLANS[company["plan"]]
    return user, (
        f"Компания *{company['title']}* подключена. Тариф «{plan['title']}»: "
        f"{company['seats']} мест, {company['session_limit']} тренировок в месяц.\n\n"
        f"Остался один шаг — рассказать боту о вашем продукте, чтобы он играл "
        f"именно ваших клиентов, а не абстрактных.\n\n"
        f"Запустите настройку: /setup"
    ), True


def welcome_unbound():
    """
    Текст для того, кто пришёл в бота без кода.

    Раньше здесь была глухая стена «доступ выдаёт компания», и человек с сайта
    уходил ни с чем. Теперь это приглашение попробовать: кнопку демо
    подставляет вызывающая сторона.
    """
    return demo.INTRO + (
        "\n\nЕсли ваша компания уже подключена — попросите руководителя "
        "прислать ссылку-приглашение."
    )
