"""Тесты модуля истории и правил ответа (пункт 9 ТЗ)."""
from __future__ import annotations

from datetime import datetime

from app import aliases, history
from app.logic import should_answer
from app.prompt import SYSTEM_PROMPT


# --- 1. «Бен» -> Дон Биткоин / Вениамин / Симанков ---------------------------
def test_alias_ben_resolves_to_don_bitcoin() -> None:
    for name in ("Бен", "Вениамин", "Симанков", "Дон Биткоин"):
        assert aliases.resolve_query_person(f"что говорил {name} про машины") == "Дон Биткоин"


# --- 2. «Илюха» -> Gorshok / Горшок / Илья / Дрочер --------------------------
def test_alias_ilyuha_resolves_to_gorshok() -> None:
    for name in ("Илюха", "Горшок", "Илья", "Дрочер", "Gorshok"):
        assert aliases.resolve_query_person(f"с кем {name} тусил") == "Gorshok"


# --- 3. Поиск/извлечение суммы премии ----------------------------------------
def test_extract_amounts() -> None:
    assert 620000 in history.extract_amounts("прилетела премия 620 тыс за квартал")
    assert 420000 in history.extract_amounts("бонус 420 тысяч рублей")
    assert 1500000 in history.extract_amounts("получил 1.5 млн")
    # мелкие числа не считаем суммами
    assert history.extract_amounts("нас было 5 человек") == []


# --- 4. Фильтрация по текущему году ------------------------------------------
def test_current_year_filter() -> None:
    now = datetime(2026, 7, 14)
    start, end = history.parse_date_filter("что писал Даня в этом году", now=now)
    assert datetime.fromtimestamp(start).year == 2026
    assert datetime.fromtimestamp(end).year == 2027
    # прошлый год
    ps, _ = history.parse_date_filter("а в прошлом году?", now=now)
    assert datetime.fromtimestamp(ps).year == 2025


# --- 5. Питер / Санкт-Петербург / СПб — один и тот же токен -------------------
def test_spb_synonyms_same_token() -> None:
    a = set(history.tokenize("Питер"))
    b = set(history.tokenize("СПб"))
    c = set(history.tokenize("Санкт-Петербург"))
    assert a & b and a & c and b & c
    assert "питер" in a and "питер" in b and "питер" in c


# --- 6. Пустая выдача — без выдуманного ответа --------------------------------
def test_no_hits_no_context() -> None:
    assert history.search("зулькарнайн флюгегехаймен ксивтщ", min_score=1.0) == []
    assert history.build_context("зулькарнайн флюгегехаймен ксивтщ") is None


# --- 7. В сравнительном вопросе бот обязан выбрать один вариант ---------------
def test_prompt_enforces_single_winner() -> None:
    p = SYSTEM_PROMPT.lower()
    assert "победител" in p                       # инструкция назвать победителя
    assert "смотря с чем сравнивать" in p         # стоп-фраза перечислена как запрет
    assert "оба варианта имеют" in p


# --- 8. Обычный вопрос в личке продолжает работать ---------------------------
def test_private_chat_always_answers() -> None:
    msg = {"text": "что лучше bmw f30 или jetour t2?"}
    assert should_answer(
        msg, bot_username="cubalizator_bot", bot_id=1,
        allow_reply_to_bot=False, is_private=True,
    )


# --- 9. Тег в группе продолжает работать -------------------------------------
def test_group_mention_answers() -> None:
    msg = {"text": "@cubalizator_bot это правда?"}
    assert should_answer(
        msg, bot_username="cubalizator_bot", bot_id=1,
        allow_reply_to_bot=False, is_private=False,
    )


# --- 10. Обычное сообщение без тега в группе игнорируется ---------------------
def test_group_plain_ignored() -> None:
    msg = {"text": "да норм всё"}
    assert not should_answer(
        msg, bot_username="cubalizator_bot", bot_id=1,
        allow_reply_to_bot=False, is_private=False,
    )


# --- интеграционные проверки на реальной истории -----------------------------
def test_person_search_returns_only_that_author() -> None:
    hits = history.search("что говорил Бен", limit=10)
    assert hits, "по Бену должны быть сообщения"
    assert all(h["author"] == "Дон Биткоин" for h in hits)


def test_year_search_returns_only_that_year() -> None:
    hits = history.search("что писал Даня в этом году", limit=10)
    assert hits
    assert all(h["author"] == "Daniil" for h in hits)
    assert all(h["date"].startswith("2026") for h in hits)


def test_spb_search_finds_питер() -> None:
    for q in ("Питер", "СПб", "Санкт-Петербург"):
        hits = history.search(q, limit=10)
        assert hits, f"по запросу {q} ожидались сообщения"


def test_build_context_format() -> None:
    ctx = history.build_context("что говорил Бен про машины")
    if ctx and ctx != "__HISTORY_UNAVAILABLE__":
        assert "Автор:" in ctx and "Дата:" in ctx and "Текст:" in ctx
