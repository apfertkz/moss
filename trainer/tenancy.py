# -*- coding: utf-8 -*-
"""
Мультиарендность: компании, сотрудники, роли, лимиты.

Главное правило модуля: любой запрос к данным тренировок обязан быть
ограничен company_id. Функции ниже — единственный законный способ узнать,
к какой компании относится пользователь. Прямых обращений к users из
других модулей быть не должно.

Тарифы задаются здесь же, чтобы цена и лимит лежали в одном месте.
"""

import os
import secrets
import string
import datetime
import logging

from . import db

log = logging.getLogger(__name__)

# --- Тарифы -----------------------------------------------------------------
# seats — сколько менеджеров можно завести, session_limit — тренировок в месяц.
PLANS = {
    "start":  {"title": "Старт",   "price_kzt": 49000,  "seats": 5,  "session_limit": 100},
    "team":   {"title": "Команда", "price_kzt": 99000,  "seats": 15, "session_limit": 300},
    "dept":   {"title": "Отдел",   "price_kzt": 199000, "seats": 40, "session_limit": 800},
    "trial":  {"title": "Пилот",   "price_kzt": 0,      "seats": 5,  "session_limit": 50},
}
DEFAULT_PLAN = "trial"

ROLE_OWNER = "owner"
ROLE_MANAGER = "manager"

STATUS_PENDING_SETUP = "pending_setup"   # оплачено, бриф ещё не заполнен
STATUS_ACTIVE = "active"                 # профиль ниши готов, можно тренироваться
STATUS_SUSPENDED = "suspended"           # подписка не продлена

# Буквы и цифры без похожих символов (0/O, 1/I/l) — коды диктуют голосом
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _code(n=8):
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


# --- Компании ---------------------------------------------------------------

def create_company(title, plan=DEFAULT_PLAN, contact_email=None, days=30):
    """Создать компанию и вернуть её вместе с кодами активации и приглашения."""
    if plan not in PLANS:
        raise ValueError(f"Неизвестный тариф: {plan}")
    p = PLANS[plan]
    expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)

    for _ in range(5):  # на случай коллизии кодов
        try:
            row = db.execute(
                """INSERT INTO companies
                   (title, plan, activation_code, invite_code, seats, session_limit,
                    expires_at, status, contact_email)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *""",
                (title, plan, _code(), _code(10), p["seats"], p["session_limit"],
                 expires, STATUS_PENDING_SETUP, contact_email),
                returning=True,
            )
            log.info("Создана компания %s (%s), код %s", title, plan, row["activation_code"])
            return row
        except Exception as e:
            if "unique" not in str(e).lower():
                raise
    raise RuntimeError("Не удалось сгенерировать уникальный код активации")


def get_company(company_id):
    return db.query("SELECT * FROM companies WHERE id=%s", (company_id,), one=True)


def company_by_activation_code(code):
    return db.query(
        "SELECT * FROM companies WHERE activation_code=%s", (code.strip().upper(),), one=True
    )


def company_by_invite_code(code):
    return db.query(
        "SELECT * FROM companies WHERE invite_code=%s", (code.strip().upper(),), one=True
    )


def set_status(company_id, status):
    db.execute("UPDATE companies SET status=%s WHERE id=%s", (status, company_id))


def set_plan(company_id, plan):
    p = PLANS[plan]
    db.execute(
        "UPDATE companies SET plan=%s, seats=%s, session_limit=%s WHERE id=%s",
        (plan, p["seats"], p["session_limit"], company_id),
    )


def rotate_invite_code(company_id):
    """Отозвать старую ссылку-приглашение, выдать новую."""
    row = db.execute(
        "UPDATE companies SET invite_code=%s WHERE id=%s RETURNING invite_code",
        (_code(10), company_id), returning=True,
    )
    return row["invite_code"]


# --- Пользователи -----------------------------------------------------------

def get_user(telegram_id):
    """Пользователь вместе с полями его компании. None — если не привязан."""
    return db.query(
        """SELECT u.*, c.title AS company_title, c.plan, c.status AS company_status,
                  c.seats, c.session_limit, c.sessions_used, c.expires_at,
                  c.invite_code, c.activation_code
           FROM users u JOIN companies c ON c.id = u.company_id
           WHERE u.telegram_id = %s""",
        (telegram_id,), one=True,
    )


def seats_taken(company_id):
    row = db.query(
        "SELECT COUNT(*) AS n FROM users WHERE company_id=%s AND active",
        (company_id,), one=True,
    )
    return row["n"]


def attach_user(telegram_id, company_id, role=ROLE_MANAGER, full_name=None, username=None):
    """
    Привязать пользователя к компании. Возвращает (user, error).
    error — текст для показа человеку, если привязать нельзя.
    """
    existing = get_user(telegram_id)
    if existing:
        if existing["company_id"] == company_id:
            return existing, None
        return None, "Этот аккаунт уже привязан к другой компании."

    if role == ROLE_MANAGER:
        company = get_company(company_id)
        if seats_taken(company_id) >= company["seats"]:
            return None, (
                f"В тарифе «{PLANS[company['plan']]['title']}» доступно "
                f"{company['seats']} мест, и все заняты. "
                f"Владельцу нужно перейти на следующий тариф."
            )

    db.execute(
        """INSERT INTO users (telegram_id, company_id, role, full_name, username)
           VALUES (%s,%s,%s,%s,%s)""",
        (telegram_id, company_id, role, full_name, username),
    )
    log.info("Пользователь %s привязан к компании %s как %s", telegram_id, company_id, role)
    return get_user(telegram_id), None


def touch(telegram_id):
    db.execute("UPDATE users SET last_seen_at=now() WHERE telegram_id=%s", (telegram_id,))


def team(company_id):
    """Список сотрудников компании со статистикой каждого."""
    return db.query(
        """SELECT u.telegram_id, u.full_name, u.username, u.role, u.active, u.last_seen_at,
                  COUNT(s.id)                                        AS total,
                  COUNT(*) FILTER (WHERE s.result='won')             AS won
           FROM users u
           LEFT JOIN sessions s ON s.user_id = u.id
           WHERE u.company_id = %s
           GROUP BY u.id
           ORDER BY won DESC NULLS LAST, total DESC""",
        (company_id,),
    )


def set_user_active(company_id, telegram_id, active):
    """Отключить/включить сотрудника. company_id обязателен — защита от чужих правок."""
    db.execute(
        "UPDATE users SET active=%s WHERE telegram_id=%s AND company_id=%s",
        (active, telegram_id, company_id),
    )


# --- Доступ и лимиты --------------------------------------------------------

class Denied(Exception):
    """Пользователю нельзя начать тренировку. В аргументе — текст для него."""


def check_can_train(user):
    """
    Проверка перед стартом тренировки. Бросает Denied с человеческим текстом.
    Порядок проверок — от самого частого к редкому.
    """
    if user is None:
        raise Denied(
            "Этот бот работает по корпоративному доступу.\n\n"
            "Если ваша компания уже подключена — попросите руководителя прислать "
            "ссылку-приглашение. Если ещё нет — оставьте заявку на moss-sale.kz"
        )

    if not user["active"]:
        raise Denied("Ваш доступ отключён руководителем.")

    if user["company_status"] == STATUS_SUSPENDED:
        raise Denied("Подписка компании приостановлена. Обратитесь к руководителю.")

    if user["company_status"] == STATUS_PENDING_SETUP:
        if user["role"] == ROLE_OWNER:
            raise Denied("Сначала заполните бриф — команда /setup.")
        raise Denied("Руководитель ещё не завершил настройку. Загляните чуть позже.")

    expires = user["expires_at"]
    if expires and expires < datetime.datetime.now(datetime.timezone.utc):
        raise Denied("Срок подписки истёк. Обратитесь к руководителю для продления.")

    if user["sessions_used"] >= user["session_limit"]:
        raise Denied(
            f"Исчерпан месячный лимит тренировок ({user['session_limit']}).\n"
            f"Руководитель может докупить пакет или перейти на следующий тариф."
        )
    return True


def consume_session(company_id):
    """
    Списать одну тренировку. Возвращает (использовано, лимит).
    Инкремент атомарный: два менеджера, закончившие одновременно, не перезапишут друг друга.
    """
    row = db.execute(
        """UPDATE companies SET sessions_used = sessions_used + 1
           WHERE id=%s RETURNING sessions_used, session_limit""",
        (company_id,), returning=True,
    )
    return row["sessions_used"], row["session_limit"]


def reset_period(company_id, days=30):
    """Обнулить счётчик при списании подписки за новый период."""
    db.execute(
        """UPDATE companies
           SET sessions_used=0, period_started_at=now(),
               expires_at = now() + (%s || ' days')::interval
           WHERE id=%s""",
        (str(days), company_id),
    )


def usage_warning(used, limit):
    """Текст предупреждения на 80% лимита — или None."""
    if limit and used == int(limit * 0.8):
        return f"⚠️ Израсходовано {used} из {limit} тренировок в этом месяце."
    return None
