# -*- coding: utf-8 -*-
"""
Проверки очеловечивания: персона, разбивка на сообщения, молчание и дожим.

Модель подменяется заглушкой — проверяем логику движка, а не качество текста.

    DATABASE_URL=... python test_human.py
"""

import json
import sys
import types

from trainer import db, tenancy as t, niche_loader, engine, persona

FAILS = []


def check(name, cond, extra=""):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def fake_client(payloads):
    seq = list(payloads)
    calls = []

    class Msgs:
        def create(self, **kw):
            calls.append(kw)
            body = seq.pop(0) if seq else "{}"
            if not isinstance(body, str):
                body = json.dumps(body, ensure_ascii=False)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(text=body)],
                usage=types.SimpleNamespace(input_tokens=900, output_tokens=200,
                                            cache_creation_input_tokens=3500,
                                            cache_read_input_tokens=0))
    c = types.SimpleNamespace(messages=Msgs())
    c._calls = calls
    return c


def make_session(profile, turns=5, silences=0, followup=False):
    sc = engine.new_scenario(profile)
    return {"scenario": sc, "profile": profile, "transcript": [],
            "turns": turns, "silences": silences,
            "awaiting_followup": followup, "last_silence_hours": 2,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0}}


def main():
    profile = niche_loader.load_file_profile("moss")

    print("\n1. Персона")
    names, moods, circs = set(), set(), set()
    for _ in range(60):
        p = persona.build("dominant", "Директор ресторана")
        names.add(p["name"]); moods.add(p["mood_id"]); circs.add(p["circumstance_id"])
    check("имена разнообразны", len(names) > 10, len(names))
    check("настроения разнообразны", len(moods) >= 5, len(moods))
    check("обстоятельства разнообразны", len(circs) >= 5, len(circs))

    dom = persona.build("dominant", "Директор")
    sys_ = persona.build("systematic", "Снабженец")
    check("властный пишет строчными и обрывками", "строчными" in dom["writing_style"])
    check("системный пишет грамотно", "грамотно" in sys_["writing_style"])
    check("у властного опечаток больше", dom["typo_rate"] > sys_["typo_rate"])
    check("голос подбирается по полу",
          persona.voice_name({"gender": "f"}) != persona.voice_name({"gender": "m"}))

    block = persona.prompt_block(dom)
    for must in ("ТЫ КАК ЖИВОЙ ЧЕЛОВЕК", "КАК ТЫ ПИШЕШЬ", dom["name"], "мессенджере"):
        check(f"в блоке промпта есть «{must[:22]}»", must in block)
    check("запрещён ассистентский тон", "Конечно!" in block)

    print("\n2. Сценарий и системный промпт")
    sc = engine.new_scenario(profile)
    check("в сценарии есть персона", "persona" in sc and sc["persona"]["name"])
    intro = engine.scenario_intro(sc)
    check("интро НЕ выдаёт имя", sc["persona"]["name"] not in intro)
    check("интро НЕ выдаёт роль", sc["status_title"].lower() not in intro.lower())
    check("интро НЕ выдаёт задачу", sc["request"][:18].lower() not in intro.lower())
    check("у сценария есть мера сдержанности", sc.get("info_guard_id") in ("soft", "medium", "hard"))
    comp = sum(1 for _ in range(200) if engine.new_scenario(profile)["has_competitor"])
    check("конкурент выпадает примерно в трети случаев", 40 < comp < 100, comp)

    prompt = engine._build_system_prompt(sc, profile)
    check("промпт содержит блок молчания", "deal_state = \"silent\"" in prompt)
    check("промпт содержит правило простыни", "простын" in prompt)
    check("промпт требует строгой оценки", "НЕ делает тебя снисходительным" in prompt)
    check("промпт просит массив сообщений", "buyer_messages" in prompt)
    check("задача помечена как скрытая", "ТВОЯ НАСТОЯЩАЯ ЗАДАЧА" in prompt)
    check("есть блок постепенного раскрытия", "ЧТО ТЫ ГОВОРИШЬ, А ЧТО ДЕРЖИШЬ ПРИ СЕБЕ" in prompt)
    check("первые реплики без деталей", "В первых двух своих репликах запрещено" in prompt)
    check("характер проявляется постепенно", "ХАРАКТЕР ПРОЯВЛЯЕТСЯ ПОСТЕПЕННО" in prompt)
    check("первые ходы оцениваются мягче", "ПЕРВЫЕ ДВА ХОДА МЕНЕДЖЕРА" in prompt)
    check("сдержанность подставлена в промпт", sc["info_guard"][:25] in prompt)
    check("запасной опенер не содержит задачу",
          all(sc["request"][:15].lower() not in o.lower() for o in engine.GENERIC_OPENERS))

    print("\n3. Разбивка ответа на несколько сообщений")
    s = make_session(profile)
    client = fake_client([{"buyer_messages": ["почем", "панно на стену", "в кофейню"],
                           "deal_state": "active", "stage": "contact", "coach_note": "ок"}])
    r = engine.step(client, s, "Здравствуйте!")
    check("вернулось три сообщения", len(r["buyer_messages"]) == 3, r["buyer_messages"])
    check("расход посчитан", r["usage"]["cache_write"] == 3500)

    client = fake_client([{"buyer_messages": ["раз","два","три","четыре","пять"],
                           "deal_state": "active"}])
    r = engine.step(client, s, "тест")
    check("больше трёх сообщений обрезается", len(r["buyer_messages"]) == 3)

    client = fake_client([{"buyer_reply": "старый формат", "deal_state": "active"}])
    r = engine.step(client, s, "тест")
    check("старый формат ответа переживается", r["buyer_messages"] == ["старый формат"])

    client = fake_client(["совсем не json"])
    r = engine.step(client, s, "тест")
    check("мусор вместо JSON не роняет ход", r["deal_state"] == "active" and r["buyer_messages"])

    print("\n4. Простыня менеджера")
    s = make_session(profile)
    client = fake_client([{"buyer_messages": ["многовато"], "deal_state": "active"}])
    engine.step(client, s, "а" * 900)
    sent = client._calls[0]["messages"][0]["content"]
    check("движок сообщает модели о простыне", "это простыня" in sent)
    check("указана длина", "900 знаков" in sent)

    # Слово «простыня» встречается ещё и в тексте разрешения на молчание,
    # поэтому проверяем именно формулировку детектора длины.
    s["scenario"]["persona"]["silence_bias"] = 0.0
    client = fake_client([{"buyer_messages": ["ок"], "deal_state": "active"}])
    engine.step(client, s, "коротко")
    check("на короткое сообщение предупреждения нет",
          "это простыня" not in client._calls[0]["messages"][0]["content"])

    print("\n5. Когда клиенту разрешено пропасть")
    s = make_session(profile, turns=1)
    check("на первых ходах не пропадает", not engine.may_go_silent(s))
    s = make_session(profile, turns=5, silences=engine.MAX_SILENCES)
    check("после лимита молчаний больше не пропадает", not engine.may_go_silent(s))
    s = make_session(profile, turns=5, followup=True)
    check("не пропадает два раза подряд", not engine.may_go_silent(s))

    s = make_session(profile, turns=9)
    s["scenario"]["persona"]["silence_bias"] = 1.0
    check("занятый клиент пропадает", engine.may_go_silent(s))
    s["scenario"]["persona"]["silence_bias"] = 0.0
    check("свободный клиент не пропадает", not engine.may_go_silent(s))

    print("\n6. Молчание и дожим")
    s = make_session(profile, turns=9)
    s["scenario"]["persona"]["silence_bias"] = 1.0
    client = fake_client([{"buyer_messages": ["не важно"], "deal_state": "silent",
                           "silence_hours": 20, "coach_note": "не запрограммировал шаг"}])
    r = engine.step(client, s, "ну как вам?")
    check("состояние молчания принято", r["deal_state"] == "silent")
    check("при молчании реплик нет", r["buyer_messages"] == [])
    check("часы переданы", r["silence_hours"] == 20)

    s2 = make_session(profile, turns=9)
    s2["scenario"]["persona"]["silence_bias"] = 0.0
    client = fake_client([{"buyer_messages": [], "deal_state": "silent", "silence_hours": 2}])
    r = engine.step(client, s2, "тест")
    check("модель не может уронить клиента в молчание самовольно",
          r["deal_state"] != "silent", r["deal_state"])

    s3 = make_session(profile, turns=9, followup=True)
    client = fake_client([{"buyer_messages": ["да, извините, был на совещании"],
                           "deal_state": "active"}])
    engine.step(client, s3, "Айгуль, посчитал ваш вариант — 380 тысяч под ключ. Замер во вторник в 10?")
    sent = client._calls[0]["messages"][0]["content"]
    check("движок помечает сообщение как дожим", "ДОЖИМ" in sent)
    check("в дожиме указано время молчания", "2 ч" in sent)

    print("\n7. Пометка о молчании")
    for hours, expect in ((2, "2 ч."), (20, "почти сутки"), (44, "двое суток")):
        m = engine.silence_marker(hours)
        check(f"{hours} ч → «{expect}»", expect in m)
    check("пометка подсказывает, чего не делать", "надумали" in engine.silence_marker(2))

    print("\n8. Стенограмма с системными пометками")
    s = make_session(profile)
    s["transcript"] = [("manager", "привет"), ("buyer", "почем"),
                       ("system", "(клиент не отвечает 2 ч.)"), ("manager", "дожим")]
    txt = engine._transcript_text(s["transcript"])
    check("системная строка не ломает стенограмму", "Система:" in txt)
    check("роли расставлены", "Менеджер:" in txt and "Покупатель:" in txt)

    test_media()




def test_media():
    """Фото и голосовые от менеджера — законные ходы, а не ошибка ввода."""
    print("\n10. Фото и голос от менеджера")
    profile = niche_loader.load_file_profile("moss")
    sc = engine.new_scenario(profile)
    prompt = engine._build_system_prompt(sc, profile)
    check("промпт объясняет строку об отправке фото", "(отправил 5 фото работ)" in prompt)
    check("показ примеров засчитывается", "засчитывай его как выполненный показ" in prompt)
    check("но одни картинки не закрывают", "ещё не закрывают" in prompt)

    s = make_session(profile)
    s["scenario"]["persona"]["silence_bias"] = 0.0
    client = fake_client([{"buyer_messages": ["о, вот это второе ничего"],
                           "deal_state": "active"}])
    r = engine.step(client, s, "(отправил 5 фото работ)")
    sent = client._calls[0]["messages"][0]["content"]
    check("ход с фото доходит до модели", "5 фото работ" in sent)
    check("клиент отвечает на фото", r["buyer_messages"] == ["о, вот это второе ничего"])
    check("фото не считается простынёй", "это простыня" not in sent)

    test_thinking_blocks()




def test_thinking_blocks():
    """
    Регресс: модели кладут первым блоком размышление без поля text.
    На Opus 4.5 это роняло каждый ход тренажёра.
    """
    print("\n11. Ответ с блоком размышления")
    from trainer import costs

    class Thinking:
        type = "thinking"
        thinking = "прикидываю, как ответить"

    class Text:
        type = "text"
        def __init__(self, t): self.text = t

    resp = types.SimpleNamespace(content=[Thinking(), Text('{"buyer_messages": ["ага"]}')],
                                 usage=None)
    check("текст достаётся из-за блока размышления",
          costs.text_of(resp) == '{"buyer_messages": ["ага"]}')

    resp2 = types.SimpleNamespace(content=[Thinking(), Text("часть один "), Text("часть два")],
                                  usage=None)
    check("несколько текстовых блоков склеиваются",
          costs.text_of(resp2) == "часть один часть два")

    check("пустой ответ не роняет", costs.text_of(types.SimpleNamespace(content=[])) == "")
    check("отсутствие content не роняет", costs.text_of(types.SimpleNamespace()) == "")
    check("только размышление → пустая строка",
          costs.text_of(types.SimpleNamespace(content=[Thinking()])) == "")

    # Полный ход через движок с таким ответом
    profile = niche_loader.load_file_profile("moss")
    s = make_session(profile)
    s["scenario"]["persona"]["silence_bias"] = 0.0

    class ThinkingClient:
        class messages:
            @staticmethod
            def create(**kw):
                return types.SimpleNamespace(
                    content=[Thinking(), Text('{"buyer_messages":["почем"],"deal_state":"active"}')],
                    usage=types.SimpleNamespace(input_tokens=10, output_tokens=5,
                                                cache_creation_input_tokens=0,
                                                cache_read_input_tokens=0))
    try:
        r = engine.step(ThinkingClient(), s, "здравствуйте")
        ok = r["buyer_messages"] == ["почем"]
    except Exception as e:
        ok = False
        print(f"      {type(e).__name__}: {e}")
    check("ход тренажёра переживает блок размышления", ok)

    print("\n" + "=" * 58)
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}: " + "; ".join(FAILS))
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    main()
