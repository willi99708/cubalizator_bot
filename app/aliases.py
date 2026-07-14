"""Словарь участников группы и разрешение алиасов.

Все варианты имени одного человека сведены к канону. Здесь же — привязка
к реальным telegram from_id из экспорта, потому что это самый надёжный
способ определить автора сообщения (ник в чате мог меняться).
"""
from __future__ import annotations

import re

# Канон -> все варианты, которыми человека называют в запросах и в чате.
ALIASES: dict[str, list[str]] = {
    "Daniil": ["Daniil", "Даня", "Даниил", "Молдаванин"],
    "Никита": ["Никита", "Некит"],
    "Ваня Авоськин": ["Ваня Авоськин", "Ваня", "Авоськин", "Берёза", "Береза", "Мел"],
    "Дон Биткоин": ["Дон Биткоин", "Бен", "Вениамин", "Симанков"],
    "Gorshok": ["Gorshok", "Горшок", "Илюха", "Илья", "Дрочер"],
    "Рома": ["Рома", "Роман", "Пользователь Пользователь"],
}

# from_id из экспорта Telegram -> канон. Самый надёжный источник авторства.
USER_ID_TO_CANON: dict[str, str] = {
    "user636876688": "Daniil",
    "user839112516": "Никита",
    "user1008691840": "Ваня Авоськин",
    "user548381092": "Дон Биткоин",
    "user398728095": "Gorshok",
    "user929563641": "Рома",
    "user5639496898": "Рома",  # второй аккаунт Ромы ("Пользователь Пользователь")
}


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е").strip()


# Обратные индексы, построенные один раз при импорте.
_DISPLAY_TO_CANON: dict[str, str] = {}
_SINGLE_WORD_ALIASES: dict[str, str] = {}   # однословный алиас -> канон
_MULTI_WORD_ALIASES: list[tuple[str, str]] = []  # (многословный алиас, канон)

for _canon, _names in ALIASES.items():
    for _name in _names:
        n = _norm(_name)
        _DISPLAY_TO_CANON[n] = _canon
        if " " in n:
            _MULTI_WORD_ALIASES.append((n, _canon))
        else:
            _SINGLE_WORD_ALIASES[n] = _canon
# длинные многословные алиасы проверяем первыми
_MULTI_WORD_ALIASES.sort(key=lambda x: -len(x[0]))

_WORD_RE = re.compile(r"[0-9a-zA-Zа-яё]+", re.UNICODE)


def canonical_for(from_id: str | None, from_name: str | None) -> str:
    """Определяет канон автора сообщения: сначала по from_id, потом по нику."""
    if from_id and from_id in USER_ID_TO_CANON:
        return USER_ID_TO_CANON[from_id]
    if from_name:
        canon = _DISPLAY_TO_CANON.get(_norm(from_name))
        if canon:
            return canon
        return from_name  # незнакомый участник — оставляем как есть
    return "неизвестный"


def resolve_query_person(text: str) -> str | None:
    """Если в тексте запроса упомянут кто-то из участников (любым алиасом),
    возвращает его канон. Иначе None. Матчит по целым словам, чтобы 'Мел'
    не срабатывал внутри 'мелочь'."""
    n = _norm(text)
    for alias, canon in _MULTI_WORD_ALIASES:
        if alias in n:
            return canon
    tokens = set(_WORD_RE.findall(n))
    for token in tokens:
        canon = _SINGLE_WORD_ALIASES.get(token)
        if canon:
            return canon
    return None


def all_aliases_for(canon: str) -> list[str]:
    return ALIASES.get(canon, [canon])
