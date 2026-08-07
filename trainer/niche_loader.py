# -*- coding: utf-8 -*-
"""
Профили ниш.

Было: JSON-файл в trainer/niches/, одна ниша на весь бот, выбор через env.
Стало: профиль хранится в базе и принадлежит компании. Файлы остались только
как эталон схемы и как заготовка для первичного наполнения (seed).

Схема профиля:
{
  "id": "moss",
  "title": "...",
  "product_context": "...",     # что продаём, кому, ключевые свойства, ценовой контекст
  "currency": "тенге",
  "statuses": [{"id","title","context"}, ...],
  "requests": ["...", ...]
}
"""

import os
import json
import glob
import logging

from . import db

log = logging.getLogger(__name__)

_NICHES_DIR = os.path.join(os.path.dirname(__file__), "niches")

REQUIRED_FIELDS = ("id", "title", "product_context", "statuses", "requests")
MIN_STATUSES = 4
MIN_REQUESTS = 6


class InvalidProfile(Exception):
    """Профиль не проходит проверку схемы."""


def validate(profile):
    """
    Проверить профиль. Бросает InvalidProfile с внятной причиной.
    Вызывается и для сгенерированных моделью профилей, и для файловых.
    """
    if not isinstance(profile, dict):
        raise InvalidProfile("Профиль должен быть объектом JSON")

    missing = [f for f in REQUIRED_FIELDS if not profile.get(f)]
    if missing:
        raise InvalidProfile(f"Не заполнены поля: {', '.join(missing)}")

    if len(str(profile["product_context"])) < 120:
        raise InvalidProfile("Описание продукта слишком короткое — бот не сможет играть клиента")

    statuses = profile["statuses"]
    if not isinstance(statuses, list) or len(statuses) < MIN_STATUSES:
        raise InvalidProfile(f"Нужно минимум {MIN_STATUSES} типов клиентов, получено {len(statuses)}")
    for i, s in enumerate(statuses):
        if not isinstance(s, dict) or not s.get("title") or not s.get("context"):
            raise InvalidProfile(f"У типа клиента №{i + 1} нет названия или описания")
        s.setdefault("id", f"status_{i + 1}")

    requests = profile["requests"]
    if not isinstance(requests, list) or len(requests) < MIN_REQUESTS:
        raise InvalidProfile(f"Нужно минимум {MIN_REQUESTS} типовых запросов, получено {len(requests)}")
    if any(not isinstance(r, str) or len(r) < 5 for r in requests):
        raise InvalidProfile("Запросы клиентов должны быть строками длиннее 5 символов")

    profile.setdefault("currency", "тенге")
    return profile


# --- Работа с базой ---------------------------------------------------------

def active_profile(company_id):
    """Действующий профиль компании. None — если ещё не настроен."""
    row = db.query(
        "SELECT profile FROM niche_profiles WHERE company_id=%s AND is_active",
        (company_id,), one=True,
    )
    return row["profile"] if row else None


def save_profile(company_id, profile, brief=None):
    """
    Сохранить новый профиль как действующий. Предыдущий уходит в историю.
    Возвращает номер версии.
    """
    validate(profile)
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version),0)+1 AS v FROM niche_profiles WHERE company_id=%s",
            (company_id,),
        ).fetchone()
        version = row["v"]
        conn.execute(
            "UPDATE niche_profiles SET is_active=FALSE WHERE company_id=%s AND is_active",
            (company_id,),
        )
        conn.execute(
            """INSERT INTO niche_profiles (company_id, version, profile, brief, is_active)
               VALUES (%s,%s,%s,%s,TRUE)""",
            (company_id, version, json.dumps(profile, ensure_ascii=False),
             json.dumps(brief, ensure_ascii=False) if brief else None),
        )
    log.info("Компания %s: сохранён профиль ниши версии %s", company_id, version)
    return version


def profile_history(company_id):
    return db.query(
        """SELECT version, created_at, profile->>'title' AS title
           FROM niche_profiles WHERE company_id=%s ORDER BY version DESC""",
        (company_id,),
    )


# --- Файловые заготовки (только для первичного наполнения) ------------------

def load_file_profile(niche_id="moss"):
    path = os.path.join(_NICHES_DIR, f"{niche_id}.json")
    if not os.path.exists(path):
        candidates = sorted(glob.glob(os.path.join(_NICHES_DIR, "*.json")))
        if not candidates:
            raise FileNotFoundError("Нет ни одной заготовки в trainer/niches/")
        path = candidates[0]
    with open(path, "r", encoding="utf-8") as f:
        return validate(json.load(f))


def available_templates():
    out = []
    for path in sorted(glob.glob(os.path.join(_NICHES_DIR, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("id"):
                out.append({"id": data["id"], "title": data.get("title", data["id"])})
        except Exception:
            continue
    return out


def describe(profile):
    """Человекочитаемое описание профиля — показываем владельцу на подтверждение."""
    lines = [
        f"*{profile['title']}*",
        "",
        f"_{profile['product_context'][:400]}_",
        "",
        f"*Типы клиентов ({len(profile['statuses'])}):*",
    ]
    lines += [f"• {s['title']}" for s in profile["statuses"]]
    lines += ["", f"*Типовые запросы ({len(profile['requests'])}):*"]
    lines += [f"• {r}" for r in profile["requests"][:8]]
    if len(profile["requests"]) > 8:
        lines.append(f"…и ещё {len(profile['requests']) - 8}")
    return "\n".join(lines)
