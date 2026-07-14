from app.logic import (
    build_model_input,
    is_mentioned,
    should_answer,
    strip_mention,
    truncate_answer,
)


def test_direct_mention() -> None:
    message = {"text": "@cubalizator_bot это правда?"}
    assert should_answer(
        message,
        bot_username="cubalizator_bot",
        bot_id=42,
        allow_reply_to_bot=False,
    )


def test_ignores_plain_message() -> None:
    message = {"text": "это правда?"}
    assert not should_answer(
        message,
        bot_username="cubalizator_bot",
        bot_id=42,
        allow_reply_to_bot=False,
    )


def test_quoted_message_and_mention() -> None:
    message = {
        "text": "@cubalizator_bot он пиздит?",
        "reply_to_message": {
            "text": "M340i с завода едет 3,2 до ста",
            "from": {"first_name": "Петя", "id": 7},
        },
    }
    result = build_model_input(message, "cubalizator_bot")
    assert "M340i с завода" in result
    assert "он пиздит" in result
    assert "Петя" in result


def test_explicit_quote_has_priority() -> None:
    message = {
        "text": "@cubalizator_bot проверь",
        "quote": {"text": "выделенная часть"},
        "reply_to_message": {"text": "всё длинное сообщение", "from": {}},
    }
    result = build_model_input(message, "cubalizator_bot")
    assert "выделенная часть" in result
    assert "всё длинное сообщение" not in result


def test_reply_to_bot_optional() -> None:
    message = {
        "text": "а точнее?",
        "reply_to_message": {"text": "ответ", "from": {"id": 42}},
    }
    assert should_answer(
        message,
        bot_username="cubalizator_bot",
        bot_id=42,
        allow_reply_to_bot=True,
    )
    assert not should_answer(
        message,
        bot_username="cubalizator_bot",
        bot_id=42,
        allow_reply_to_bot=False,
    )


def test_mention_helpers() -> None:
    assert is_mentioned("эй @CubaLizator_Bot, сюда", "cubalizator_bot")
    assert strip_mention("@cubalizator_bot, проверь", "cubalizator_bot") == "проверь"


def test_truncate() -> None:
    assert truncate_answer("коротко", 20) == "коротко"
    assert len(truncate_answer("очень длинный ответ из многих слов", 16)) <= 16
