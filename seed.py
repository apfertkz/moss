# -*- coding: utf-8 -*-
"""
Первичное наполнение: создать компанию и назначить ей профиль ниши
из заготовки trainer/niches/*.json.

Нужен, пока не готов мастер брифа (этап 2). Запуск:
    python seed.py "MOSS Алматы" team moss
"""
import sys
from trainer import db, tenancy, niche_loader

def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "Демо-компания"
    plan = sys.argv[2] if len(sys.argv) > 2 else "trial"
    template = sys.argv[3] if len(sys.argv) > 3 else "moss"

    db.init_db()
    company = tenancy.create_company(title, plan=plan)
    profile = niche_loader.load_file_profile(template)
    version = niche_loader.save_profile(company["id"], profile)
    tenancy.set_status(company["id"], tenancy.STATUS_ACTIVE)

    print(f"Компания:       {title} (id={company['id']}, тариф {plan})")
    print(f"Профиль ниши:   {profile['title']} (версия {version})")
    print(f"Код активации:  {company['activation_code']}")
    print(f"Код приглашения:{company['invite_code']}")
    print()
    print(f"Ссылка владельцу:  t.me/<BOT>?start={company['activation_code']}")
    print(f"Ссылка менеджерам: t.me/<BOT>?start=join_{company['invite_code']}")

if __name__ == "__main__":
    main()
