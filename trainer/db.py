# -*- coding: utf-8 -*-
"""
Слой базы данных. PostgreSQL через psycopg3 с пулом соединений.

Зачем Postgres вместо прежней SQLite/памяти: контейнер на Railway эфемерный,
файловая система обнуляется при каждом деплое. Для продукта, где клиент платит
за накопленную статистику отдела, это недопустимо.

Подключение — через переменную окружения DATABASE_URL (Railway выдаёт её
автоматически при добавлении Postgres в проект).

Схема создаётся при старте (init_db) и безопасна к повторному запуску.
"""

import os
import logging

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

_pool = None


def _normalize_url(url: str) -> str:
    """Railway иногда отдаёт схему postgres:// — psycopg ждёт postgresql://."""
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "Не задана переменная окружения DATABASE_URL. "
                "Добавь Postgres в проект Railway — переменная появится сама."
            )
        _pool = ConnectionPool(
            _normalize_url(DATABASE_URL),
            min_size=1,
            max_size=int(os.environ.get("DB_POOL_MAX", "10")),
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


class _Conn:
    """Контекст-менеджер: соединение из пула с автокоммитом транзакции."""

    def __enter__(self):
        self._cm = get_pool().connection()
        self._conn = self._cm.__enter__()
        return self._conn

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)


def connection():
    return _Conn()


def query(sql, params=(), one=False):
    """SELECT. Возвращает список словарей либо один словарь при one=True."""
    with connection() as conn:
        cur = conn.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()


def execute(sql, params=(), returning=False):
    """INSERT/UPDATE/DELETE. При returning=True вернёт первую строку RETURNING."""
    with connection() as conn:
        cur = conn.execute(sql, params)
        return cur.fetchone() if returning else None


SCHEMA = """
-- Компания-клиент. Один экземпляр бота обслуживает много компаний.
CREATE TABLE IF NOT EXISTS companies (
    id                BIGSERIAL PRIMARY KEY,
    title             TEXT        NOT NULL,
    plan              TEXT        NOT NULL DEFAULT 'start',
    activation_code   TEXT        NOT NULL UNIQUE,
    invite_code       TEXT        NOT NULL UNIQUE,
    seats             INTEGER     NOT NULL DEFAULT 5,
    session_limit     INTEGER     NOT NULL DEFAULT 100,
    sessions_used     INTEGER     NOT NULL DEFAULT 0,
    period_started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ,
    status            TEXT        NOT NULL DEFAULT 'pending_setup',
    contact_email     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Сотрудники компаний. Один telegram_id принадлежит ровно одной компании.
CREATE TABLE IF NOT EXISTS users (
    id           BIGSERIAL PRIMARY KEY,
    telegram_id  BIGINT      NOT NULL UNIQUE,
    company_id   BIGINT      NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    role         TEXT        NOT NULL DEFAULT 'manager',
    full_name    TEXT,
    username     TEXT,
    active       BOOLEAN     NOT NULL DEFAULT TRUE,
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_users_company ON users(company_id);

-- Профиль ниши: то, что раньше лежало файлом в trainer/niches/.
-- Версионируется: правка создаёт новую версию, старая остаётся в истории.
CREATE TABLE IF NOT EXISTS niche_profiles (
    id          BIGSERIAL PRIMARY KEY,
    company_id  BIGINT      NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    version     INTEGER     NOT NULL DEFAULT 1,
    profile     JSONB       NOT NULL,
    brief       JSONB,
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_niche_company ON niche_profiles(company_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_niche_one_active
    ON niche_profiles(company_id) WHERE is_active;

-- Завершённые тренировки.
CREATE TABLE IF NOT EXISTS sessions (
    id            BIGSERIAL PRIMARY KEY,
    company_id    BIGINT      NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id       BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    telegram_id   BIGINT      NOT NULL,
    psychotype_id TEXT,
    status_title  TEXT,
    request       TEXT,
    result        TEXT        NOT NULL,
    turns         INTEGER     NOT NULL DEFAULT 0,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_company ON sessions(company_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Реплики диалогов — для разбора и для доказательной базы перед клиентом.
CREATE TABLE IF NOT EXISTS messages (
    id         BIGSERIAL PRIMARY KEY,
    session_id BIGINT      NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    company_id BIGINT      NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    role       TEXT        NOT NULL,
    text       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

-- Учёт расхода: сколько токенов и денег съел каждый клиент.
-- Без этого маржа по клиенту неизвестна и лимиты не на чем строить.
CREATE TABLE IF NOT EXISTS usage_log (
    id             BIGSERIAL PRIMARY KEY,
    company_id     BIGINT      NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    telegram_id    BIGINT,
    kind           TEXT        NOT NULL,
    model          TEXT,
    input_tokens   INTEGER     NOT NULL DEFAULT 0,
    output_tokens  INTEGER     NOT NULL DEFAULT 0,
    cache_write    INTEGER     NOT NULL DEFAULT 0,
    cache_read     INTEGER     NOT NULL DEFAULT 0,
    cost_usd       NUMERIC(12,6) NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_usage_company_date ON usage_log(company_id, created_at);

-- Активные тренировки. Раньше жили в памяти процесса: любой деплой обрывал
-- диалог молча, менеджер писал в пустоту. Теперь переживают перезапуск.
CREATE TABLE IF NOT EXISTS active_sessions (
    telegram_id BIGINT      PRIMARY KEY,
    company_id  BIGINT      NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    data        JSONB       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_active_sessions_company ON active_sessions(company_id);

-- Журнал изменений: кто, что и над кем сделал. Нужен и для разбора
-- «я не трогал», и чтобы видеть историю клиента в его карточке.
CREATE TABLE IF NOT EXISTS admin_log (
    id          BIGSERIAL   PRIMARY KEY,
    actor       TEXT        NOT NULL,
    action      TEXT        NOT NULL,
    company_id  BIGINT      REFERENCES companies(id) ON DELETE SET NULL,
    telegram_id BIGINT,
    details     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_admin_log_company ON admin_log(company_id, created_at DESC);

-- Что уже напомнили клиенту, чтобы не слать одно и то же каждый день.
CREATE TABLE IF NOT EXISTS reminders_sent (
    company_id BIGINT      NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    kind       TEXT        NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    sent_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, kind, period_end)
);

-- Тарифы. Раньше жили в коде: любая правка цены означала деплой.
-- Значения по умолчанию засеваются при первом запуске и дальше
-- редактируются из панели.
CREATE TABLE IF NOT EXISTS plans (
    key           TEXT    PRIMARY KEY,
    title         TEXT    NOT NULL,
    price_kzt     INTEGER NOT NULL DEFAULT 0,
    seats         INTEGER NOT NULL DEFAULT 5,
    session_limit INTEGER NOT NULL DEFAULT 100,
    sort          INTEGER NOT NULL DEFAULT 0
);
"""




def init_db():
    """Создать схему. Безопасно вызывать при каждом старте бота."""
    with connection() as conn:
        conn.execute(SCHEMA)
    log.info("Схема базы данных готова")


def healthcheck() -> bool:
    try:
        query("SELECT 1 AS ok", one=True)
        return True
    except Exception as e:
        log.error("База недоступна: %s", e)
        return False
