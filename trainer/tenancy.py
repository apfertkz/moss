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
#
# Один тариф на всех, разница только в сроке оплаты.
#
# Тарифы по числу мест были ошибкой: место нам ничего не стоит, расход
# создают тренировки. Мы брали деньги за бесплатное и штрафовали клиента
# ровно за то поведение, которое нам нужно, — за подключение ещё одного
# менеджера. Он его не подключал, отдел не тренировался, через месяц не
# продлевал. Теперь ограничитель один и он же источник расхода — лимит
# тренировок; мест хватает на типовой отдел, а кому мало, тем считаем руками.
#
# seats — сколько менеджеров можно завести, session_limit — тренировок в месяц.
#
# «Первые десять» — тот же продукт по цене входа. Это не второй тариф в
# сетке, а зафиксированная за ранними клиентами цена: со списочной легко
# скинуть, обратно подняться нельзя, поэтому дешёвый вход оформлен как
# именованное условие с понятным концом, а не как новый прайс.
PLANS = {
    "base":  {"title": "Отдел",         "price_kzt": 79000, "seats": 8, "session_limit": 200},
    "early": {"title": "Первые десять", "price_kzt": 55000, "seats": 8, "session_limit": 200},
    "trial": {"title": "Пилот",         "price_kzt": 0,     "seats": 5, "session_limit": 50},
}
DEFAULT_PLAN = "trial"

# Тарифы, от которых отказались. Компании на них переводим на базовый,
# сохраняя уже выданные места и лимиты: клиент купил их за свои деньги.
LEGACY_PLANS = ("start", "team", "dept")

# Цена первых десяти обещана навсегда — значит, и мест должно быть ровно
# десять, иначе обещание перестаёт что-либо значить.
EARLY_PLAN = "early"
EARLY_LIMIT = 10

# Сколько просить за человека сверх восьмого. В коде не применяется —
# места добавляются из панели вручную; это ориентир, чтобы на переговорах
# не придумывать цифру заново.
EXTRA_SEAT_KZT = 8000

# Длина оплаченного периода. Счётчик тренировок обнуляется, когда он истёк.
PERIOD_DAYS = 30

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
                  c.period_started_at, c.invite_code, c.activation_code
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


def roll_period_if_due(user):
    """
    Начать новый период, если старый истёк.

    Сделано лениво, при первом обращении, а не по расписанию: внешнего
    планировщика у нас нет, а забытый вызов означал бы, что клиент упёрся
    в исчерпанный лимит и на второй месяц. Возвращает актуального
    пользователя — тот же объект либо перечитанный из базы.
    """
    started = user.get("period_started_at")
    if not started:
        return user

    now = datetime.datetime.now(datetime.timezone.utc)
    if (now - started).days < PERIOD_DAYS:
        return user

    # Подписка кончилась — период не продлеваем, иначе клиент тренируется бесплатно.
    expires = user.get("expires_at")
    if expires and expires < now:
        return user

    db.execute(
        """UPDATE companies SET sessions_used = 0, period_started_at = now()
           WHERE id = %s AND period_started_at = %s""",
        (user["company_id"], started),
    )
    log.info("Компания %s: начат новый период", user["company_id"])
    return get_user(user["telegram_id"]) or user


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

    user = roll_period_if_due(user)

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


# --- Управление подпиской ---------------------------------------------------
#
# Всё, что меняет условия клиента, живёт здесь и возвращает обновлённую
# компанию. Панель и команды бота вызывают эти функции, а не пишут в базу
# напрямую — иначе изменения расползутся по коду и журнал перестанет быть полным.

def extend(company_id, days=PERIOD_DAYS, reset_usage=True):
    """
    Продлить подписку. По умолчанию заодно начинает новый период:
    клиент заплатил — счётчик тренировок должен обнулиться.

    Срок считается от большей из двух дат: текущего окончания или сегодня.
    Иначе продление просроченной подписки съедало бы дни простоя.
    """
    return db.execute(
        """UPDATE companies
              SET expires_at = GREATEST(COALESCE(expires_at, now()), now())
                               + (%s || ' days')::interval,
                  status = CASE WHEN status = %s THEN %s ELSE status END,
                  sessions_used = CASE WHEN %s THEN 0 ELSE sessions_used END,
                  period_started_at = CASE WHEN %s THEN now() ELSE period_started_at END
            WHERE id = %s
        RETURNING *""",
        (str(days), STATUS_SUSPENDED, STATUS_ACTIVE, reset_usage, reset_usage, company_id),
        returning=True,
    )


def change_plan(company_id, plan):
    """
    Перевести на другой тариф: меняются и места, и лимит тренировок.

    Использованное не обнуляем — клиент мог перейти в середине месяца,
    и обнуление подарило бы ему второй лимит.
    """
    if plan not in PLANS:
        raise ValueError(f"Неизвестный тариф: {plan}")
    p = PLANS[plan]
    return db.execute(
        """UPDATE companies SET plan=%s, seats=%s, session_limit=%s
            WHERE id=%s RETURNING *""",
        (plan, p["seats"], p["session_limit"], company_id), returning=True,
    )


def add_seats(company_id, n):
    """Добавить или убрать места. В минус не уходим."""
    return db.execute(
        "UPDATE companies SET seats = GREATEST(1, seats + %s) WHERE id=%s RETURNING *",
        (n, company_id), returning=True,
    )


def add_sessions(company_id, n):
    """Докупленный пакет тренировок: поднимаем потолок текущего периода."""
    return db.execute(
        """UPDATE companies SET session_limit = GREATEST(0, session_limit + %s)
            WHERE id=%s RETURNING *""",
        (n, company_id), returning=True,
    )


def suspend(company_id):
    return set_status(company_id, STATUS_SUSPENDED)


def resume(company_id):
    return set_status(company_id, STATUS_ACTIVE)


def days_left(company):
    """Сколько дней осталось. None — если срок не задан, 0 — если истёк."""
    expires = company.get("expires_at")
    if not expires:
        return None
    delta = expires - datetime.datetime.now(datetime.timezone.utc)
    return max(0, delta.days)


def expiring(days=7):
    """Компании, у которых подписка кончается в ближайшие N дней."""
    return db.query(
        """SELECT * FROM companies
            WHERE status <> %s
              AND expires_at IS NOT NULL
              AND expires_at BETWEEN now() AND now() + (%s || ' days')::interval
         ORDER BY expires_at""",
        (STATUS_SUSPENDED, str(days)),
    )


def promote_to_owner(telegram_id, company_id):
    """
    Сделать владельцем того, кто уже числится в этой компании.

    Нужно для случая, когда компанию активируют с аккаунта, ранее вошедшего
    по ссылке-приглашению. Раньше attach_user молча возвращал существующую
    запись, роль оставалась менеджерской, и компания навсегда оставалась без
    руководителя: кабинет выдавать было некому, отчёт отправлять — тоже.
    """
    db.execute(
        "UPDATE users SET role=%s WHERE telegram_id=%s AND company_id=%s",
        (ROLE_OWNER, telegram_id, company_id),
    )
    log_action("system", "user.promote", company_id, telegram_id, "менеджер → владелец")
    return get_user(telegram_id)


def owner_of(company_id):
    return db.query(
        "SELECT * FROM users WHERE company_id=%s AND role=%s LIMIT 1",
        (company_id, ROLE_OWNER), one=True,
    )


# --- Журнал действий --------------------------------------------------------

def log_action(actor, action, company_id=None, telegram_id=None, details=None):
    """
    Записать изменение. Пишем всегда, даже если действие сделано из бота:
    в карточке клиента должна быть видна вся история, а не половина.
    """
    import json
    db.execute(
        """INSERT INTO admin_log (actor, action, company_id, telegram_id, details)
           VALUES (%s,%s,%s,%s,%s)""",
        (str(actor), action, company_id, telegram_id,
         json.dumps(details, ensure_ascii=False) if details else None),
    )


def history(company_id, limit=50):
    return db.query(
        """SELECT * FROM admin_log WHERE company_id=%s
         ORDER BY created_at DESC LIMIT %s""",
        (company_id, limit),
    )


# --- Тарифы в базе ----------------------------------------------------------
#
# PLANS остаётся обычным словарём: на него завязан весь код, и менять его тип
# ради панели было бы дороже, чем поддерживать синхронизацию. База — источник
# правды, словарь — кэш, который обновляется при старте и после правки.

DEFAULT_PLANS = dict(PLANS)


def sync_catalog():
    """
    Привести содержимое базы к тому, что объявлено в коде.

    Что делает: заводит недостающие тарифы и цены, убирает сетку по местам
    и сроки, которые мы больше не продаём. Чего не делает — не переписывает
    существующие цены: их правят из панели, и правка не должна отменяться
    следующим деплоем.

    Возвращает True, если что-то изменила: вызывающему нужно перечитать.

    Места и лимиты компаний не трогаем. Клиент на «Отделе» купил сорок мест
    за свои деньги; перевод на базовый тариф урезал бы его до восьми — это
    было бы не переименование, а отъём оплаченного.
    """
    changed = False

    known = {r["key"] for r in (db.query("SELECT key FROM plans") or [])}

    stale = known & set(LEGACY_PLANS)
    if stale:
        moved = db.query(
            "UPDATE companies SET plan='base' WHERE plan = ANY(%s) RETURNING id",
            (list(LEGACY_PLANS),),
        ) or []
        db.execute("DELETE FROM plan_prices WHERE plan_key = ANY(%s)", (list(LEGACY_PLANS),))
        db.execute("DELETE FROM plans WHERE key = ANY(%s)", (list(LEGACY_PLANS),))
        log.info("Старые тарифы убраны, компаний переведено: %s", len(moved))
        changed = True

    for i, (key, p) in enumerate(DEFAULT_PLANS.items()):
        if key in known:
            continue
        db.execute(
            """INSERT INTO plans (key, title, price_kzt, seats, session_limit, sort)
                    VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (key) DO NOTHING""",
            (key, p["title"], p["price_kzt"], p["seats"], p["session_limit"], i),
        )
        for months, price in DEFAULT_PRICES.get(key, {}).items():
            db.execute(
                """INSERT INTO plan_prices (plan_key, months, price_kzt) VALUES (%s,%s,%s)
                   ON CONFLICT (plan_key, months) DO NOTHING""",
                (key, months, price),
            )
        log.info("Заведён тариф %s", key)
        changed = True

    dropped = db.query(
        "DELETE FROM plan_prices WHERE months <> ALL(%s) RETURNING plan_key, months",
        (list(TERMS),),
    ) or []
    if dropped:
        log.info("Убраны сроки, которые больше не продаём: %s", dropped)
        changed = True

    return changed


def early_left():
    """
    Сколько ещё компаний можно взять по цене первых десяти.

    Считаем по факту, а не счётчиком в настройках: счётчик разъезжается с
    реальностью при первой же отмене, а этот ответ всегда верен.
    """
    row = db.query("SELECT COUNT(*) AS n FROM companies WHERE plan=%s",
                   (EARLY_PLAN,), one=True) or {}
    return max(0, EARLY_LIMIT - int(row.get("n") or 0))


def load_plans():
    """Подтянуть тарифы из базы в PLANS. При первом запуске засевает значения."""
    try:
        rows = db.query("SELECT * FROM plans ORDER BY sort, price_kzt")
    except Exception:
        log.exception("Не удалось прочитать тарифы — остаёмся на значениях из кода")
        return PLANS

    if not rows:
        for i, (key, p) in enumerate(DEFAULT_PLANS.items()):
            db.execute(
                """INSERT INTO plans (key, title, price_kzt, seats, session_limit, sort)
                        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (key) DO NOTHING""",
                (key, p["title"], p["price_kzt"], p["seats"], p["session_limit"], i),
            )
        log.info("Тарифы засеяны значениями из кода")
        rows = db.query("SELECT * FROM plans ORDER BY sort, price_kzt") or []
    elif sync_catalog():
        rows = db.query("SELECT * FROM plans ORDER BY sort, price_kzt") or []

    PLANS.clear()
    for r in rows:
        PLANS[r["key"]] = {
            "title": r["title"], "price_kzt": r["price_kzt"],
            "seats": r["seats"], "session_limit": r["session_limit"],
        }
    return PLANS


def save_plan(key, title=None, price_kzt=None, seats=None, session_limit=None):
    """
    Изменить тариф. Уже подключённым компаниям места и лимиты не пересчитываем:
    это отдельное решение по каждому клиенту, а не побочный эффект правки цены.
    """
    current = PLANS.get(key, {})
    row = db.execute(
        """INSERT INTO plans (key, title, price_kzt, seats, session_limit)
                VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (key) DO UPDATE SET
                title = EXCLUDED.title, price_kzt = EXCLUDED.price_kzt,
                seats = EXCLUDED.seats, session_limit = EXCLUDED.session_limit
        RETURNING *""",
        (key,
         title if title is not None else current.get("title", key),
         int(price_kzt if price_kzt is not None else current.get("price_kzt", 0)),
         int(seats if seats is not None else current.get("seats", 5)),
         int(session_limit if session_limit is not None else current.get("session_limit", 100))),
        returning=True,
    )
    load_plans()
    return row


# --- Сроки оплаты -----------------------------------------------------------
#
# Помесячная оплата продаёт продукт заново каждые тридцать дней, а внедрение
# столько не занимает. Длинные сроки покупают время: три месяца — это ровно
# столько, сколько нужно, чтобы отдел дошёл до первых цифр, а цифры продлевают
# подписку сами.
#
# Скидка растёт со сроком: минус десять, пятнадцать и двадцать пять процентов.

# Три варианта, а не четыре: три выбирают, четыре обдумывают.
TERMS = (1, 3, 12)

# Скидка за срок — единственное, чем отличаются предложения. Числа круглые
# намеренно: 213 000 читается как результат работы калькулятора, 210 000 —
# как решение.
DEFAULT_PRICES = {
    "base":  {1: 79000, 3: 210000, 12: 700000},
    "early": {1: 55000, 3: 150000, 12: 530000},
    "trial": {1: 0},
}

# plan_key -> {months: price}. Кэш поверх базы, как и PLANS.
PRICES = {}


def load_prices():
    """Подтянуть цены по срокам. При первом запуске засевает значения."""
    try:
        rows = db.query("SELECT * FROM plan_prices")
    except Exception:
        log.exception("Не удалось прочитать цены — остаёмся на значениях из кода")
        PRICES.update({k: dict(v) for k, v in DEFAULT_PRICES.items()})
        return PRICES

    if not rows:
        for plan, terms in DEFAULT_PRICES.items():
            for months, price in terms.items():
                db.execute(
                    """INSERT INTO plan_prices (plan_key, months, price_kzt)
                            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (plan, months, price),
                )
        log.info("Цены по срокам засеяны значениями из кода")
        rows = db.query("SELECT * FROM plan_prices") or []

    PRICES.clear()
    for r in rows:
        PRICES.setdefault(r["plan_key"], {})[int(r["months"])] = int(r["price_kzt"])
    return PRICES


def save_price(plan_key, months, price_kzt):
    db.execute(
        """INSERT INTO plan_prices (plan_key, months, price_kzt) VALUES (%s,%s,%s)
           ON CONFLICT (plan_key, months) DO UPDATE SET price_kzt = EXCLUDED.price_kzt""",
        (plan_key, int(months), int(price_kzt)),
    )
    load_prices()
    return PRICES.get(plan_key, {})


def price_for(plan_key, months):
    """Цена срока. Если срок не задан явно — считаем по месячной без скидки."""
    terms = PRICES.get(plan_key) or DEFAULT_PRICES.get(plan_key) or {}
    if months in terms:
        return terms[months]
    monthly = terms.get(1) or PLANS.get(plan_key, {}).get("price_kzt", 0)
    return monthly * months


def extend_months(company_id, months=1, reset_usage=True):
    """
    Продлить на календарные месяцы.

    Именно месяцы, а не тридцать дней: клиент, оплативший год 31 января,
    заметит, что доступ кончился на пять дней раньше обещанного.

    Срок считается от большей из дат — текущего окончания или сегодня,
    иначе продление просроченной подписки съедало бы дни простоя.
    """
    return db.execute(
        """UPDATE companies
              SET expires_at = GREATEST(COALESCE(expires_at, now()), now())
                               + (%s || ' months')::interval,
                  status = CASE WHEN status = %s THEN %s ELSE status END,
                  sessions_used = CASE WHEN %s THEN 0 ELSE sessions_used END,
                  period_started_at = CASE WHEN %s THEN now() ELSE period_started_at END
            WHERE id = %s
        RETURNING *""",
        (str(int(months)), STATUS_SUSPENDED, STATUS_ACTIVE, reset_usage, reset_usage, company_id),
        returning=True,
    )


# --- Поступления ------------------------------------------------------------

def record_payment(company_id, months, amount_kzt, plan=None, note=None):
    """Записать оплату. Сумма может отличаться от прайса — бывают договорённости."""
    return db.execute(
        """INSERT INTO payments (company_id, plan, months, amount_kzt, note)
                VALUES (%s,%s,%s,%s,%s) RETURNING *""",
        (company_id, plan, int(months), int(amount_kzt), note), returning=True,
    )


def payments_of(company_id, limit=20):
    return db.query(
        "SELECT * FROM payments WHERE company_id=%s ORDER BY created_at DESC LIMIT %s",
        (company_id, limit),
    ) or []
