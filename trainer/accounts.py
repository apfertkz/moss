# -*- coding: utf-8 -*-
"""
Доступ руководителей к панели.

Директор получает ссылку, свой telegram id как логин и временный пароль
сразу при подключении компании. При первом входе панель требует сменить
пароль и не даёт ничего сделать, пока он этого не сделает.

Почему id, а не почта: почту при подключении никто не оставляет, а id уже
известен — человек пришёл по ссылке в Telegram. Заодно логин невозможно
перепутать: он один и тот же и в боте, и в панели.

Пароль хранится как хэш с солью. Открытым текстом он существует ровно один
раз — в сообщении с временным паролем, и то до первой смены.
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets

from . import db

log = logging.getLogger(__name__)

# Итераций столько, чтобы подбор был дорогим, а вход — незаметным.
ITERATIONS = 200_000
# Без похожих символов: временный пароль диктуют голосом и переписывают руками.
ALPHABET = "abcdefghijkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789"
TEMP_LENGTH = 10
MIN_LENGTH = 8


def panel_url():
    """
    Адрес панели для писем руководителям. Railway отдаёт домен в
    RAILWAY_PUBLIC_DOMAIN, но свой домен важнее — его и спрашиваем первым.
    """
    url = os.environ.get("PANEL_URL") or os.environ.get("RAILWAY_PUBLIC_DOMAIN") or ""
    url = url.strip().rstrip("/")
    if url and not url.startswith("http"):
        url = "https://" + url
    return url


def temp_password(n=TEMP_LENGTH):
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def hash_password(password, salt=None):
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify(password, stored):
    """Сравнение постоянного времени: иначе по задержке можно подбирать посимвольно."""
    try:
        salt_b64, digest_b64 = stored.split("$", 1)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except Exception:
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return hmac.compare_digest(got, expected)


# --- Учётные записи ---------------------------------------------------------

def get(telegram_id):
    return db.query("SELECT * FROM panel_accounts WHERE telegram_id=%s",
                    (telegram_id,), one=True)


def create(telegram_id, company_id, password=None):
    """
    Завести доступ. Возвращает пароль открытым текстом — его нужно
    показать человеку прямо сейчас, второй возможности не будет.
    """
    password = password or temp_password()
    db.execute(
        """INSERT INTO panel_accounts (telegram_id, company_id, password_hash, must_change)
                VALUES (%s,%s,%s,TRUE)
           ON CONFLICT (telegram_id) DO UPDATE SET
                password_hash = EXCLUDED.password_hash,
                company_id = EXCLUDED.company_id,
                must_change = TRUE""",
        (telegram_id, company_id, hash_password(password)),
    )
    log.info("Заведён доступ в панель для %s (компания %s)", telegram_id, company_id)
    return password


def set_password(telegram_id, password):
    """Смена пароля самим руководителем. Снимает требование сменить."""
    db.execute(
        """UPDATE panel_accounts SET password_hash=%s, must_change=FALSE
            WHERE telegram_id=%s""",
        (hash_password(password), telegram_id),
    )


def reset(telegram_id):
    """Сброс владельцем продукта: выдаёт новый временный пароль."""
    account = get(telegram_id)
    if not account:
        return None
    return create(telegram_id, account["company_id"])


def touch(telegram_id):
    db.execute("UPDATE panel_accounts SET last_login_at=now() WHERE telegram_id=%s",
               (telegram_id,))


def check(telegram_id, password):
    """
    Проверить логин и пароль. Возвращает запись либо None.

    Проверку пароля выполняем даже для несуществующего логина: иначе по
    времени ответа видно, какие id заведены в системе.
    """
    account = get(telegram_id)
    stored = account["password_hash"] if account else hash_password("нет такого")
    ok = verify(password, stored)
    if not account or not ok:
        return None
    touch(telegram_id)
    return account


def problems(password):
    """Что не так с новым паролем. Пустой список — всё в порядке."""
    issues = []
    if len(password or "") < MIN_LENGTH:
        issues.append(f"не короче {MIN_LENGTH} символов")
    if password and password.isdigit():
        issues.append("не только цифры")
    if password and password.lower() in ("password", "пароль", "12345678", "qwertyui"):
        issues.append("не такой очевидный")
    return issues


# --- Тексты -----------------------------------------------------------------

def welcome_text(url, telegram_id, password):
    return (
        "🔐 *Личный кабинет руководителя*\n\n"
        "Здесь видно, кто из менеджеров как продаёт, с какими типами клиентов "
        "отдел справляется хуже всего и сколько тренировок осталось.\n\n"
        f"Адрес: {url}\n"
        f"Логин: `{telegram_id}`\n"
        f"Временный пароль: `{password}`\n\n"
        "При первом входе панель попросит сменить пароль — это обязательно."
    )


def changed_text(url, telegram_id):
    return (
        "✅ Пароль изменён.\n\n"
        f"Вход: {url}\n"
        f"Логин: `{telegram_id}`\n\n"
        "Сам пароль не пишем — сообщение с ним осталось бы в переписке навсегда. "
        "Если забудете, напишите нам, и мы выдадим новый временный."
    )
