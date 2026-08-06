# -*- coding: utf-8 -*-
"""
Загрузчик ниша-паков. Ниша — это отдельный подключаемый JSON в trainer/niches/.

МОДУЛЬНОСТЬ: чтобы перенести тренажёр на другой бизнес, достаточно положить
новый JSON в trainer/niches/ по той же схеме (см. moss.json) и указать его id
в переменной окружения TRAINER_NICHE. Психотипы и алгоритм при этом не меняются.

Схема ниша-пака (JSON):
{
  "id": "moss",
  "title": "...",                 # человекочитаемое название ниши
  "product_context": "...",       # что продаём, кому, ключевые свойства, ценовой контекст
  "currency": "тенге",            # валюта для реплик покупателя
  "statuses":  [{"id","title","context"}, ...],   # кем выпадает покупатель
  "requests":  ["...", ...]        # с каким запросом он приходит
}
"""

import os
import json
import glob

_NICHES_DIR = os.path.join(os.path.dirname(__file__), "niches")
_cache = {}


def _load_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def available_niches():
    """Список id всех ниша-паков в папке."""
    ids = []
    for path in glob.glob(os.path.join(_NICHES_DIR, "*.json")):
        try:
            data = _load_file(path)
            if data.get("id"):
                ids.append(data["id"])
        except Exception:
            continue
    return ids


def load_niche(niche_id=None):
    """
    Загрузить ниша-пак. Если id не задан — берём из env TRAINER_NICHE,
    по умолчанию 'moss'. Результат кешируется.
    """
    niche_id = niche_id or os.environ.get("TRAINER_NICHE", "moss")
    if niche_id in _cache:
        return _cache[niche_id]
    path = os.path.join(_NICHES_DIR, f"{niche_id}.json")
    if not os.path.exists(path):
        # запасной вариант — первый доступный пак
        candidates = glob.glob(os.path.join(_NICHES_DIR, "*.json"))
        if not candidates:
            raise FileNotFoundError("Не найдено ни одного ниша-пака в trainer/niches/")
        path = candidates[0]
    data = _load_file(path)
    _cache[data.get("id", niche_id)] = data
    return data
