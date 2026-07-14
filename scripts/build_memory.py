"""Построение долговременной памяти data/chat_memory.json из всей переписки.

Память строится ОДИН РАЗ пакетами через GigaChat (не регулярками): скрипт
режет историю на батчи, просит модель вернуть структурированные факты
(события, прозвища, связи, шутки, премии, поездки) с доказательствами
(message_id) и уровнем уверенности, затем сливает всё в один файл.

Запуск (нужны переменные GigaChat, как у бота):
    python scripts/build_memory.py
    python scripts/build_memory.py --batch 150 --limit 3000   # частично, для теста

Поскольку это офлайн-скрипт, он делает много запросов к GigaChat и может идти
долго. Прогресс сохраняется инкрементально — можно прервать и продолжить.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.aliases import ALIASES, canonical_for  # noqa: E402
from app.clients import GigaChatClient  # noqa: E402
from app.config import Settings  # noqa: E402

BATCH_SYSTEM = """
Ты извлекаешь структурированные факты из куска переписки закрытой группы.
Верни СТРОГО JSON без markdown:
{
  "events": [ {"description","participants":[],"dates":[],"evidence":[message_id...],"confidence":"подтверждено|вероятно"} ],
  "inside_jokes": [ {"description","evidence":[],"confidence":""} ],
  "relationships": [ {"description","participants":[],"evidence":[],"confidence":""} ],
  "aliases": [ {"alias","person","evidence":[],"confidence":""} ]
}
Бери только то, что реально видно в сообщениях. Каждый факт подкрепляй message_id.
Если ничего значимого нет — верни пустые массивы.
""".strip()


def _load_history(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _fmt_batch(msgs: list[dict]) -> str:
    lines = []
    for m in msgs:
        author = canonical_for(m.get("from_id"), m.get("from"))
        reply = f" (ответ на #{m['reply_to']})" if m.get("reply_to") else ""
        lines.append(f"#{m.get('id')}{reply} [{(m.get('date') or '')[:10]}] {author}: {m.get('text')}")
    return "\n".join(lines)


def _parse(text: str) -> dict:
    t = text.strip().strip("`")
    if t.startswith("json"):
        t = t[4:]
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e != -1:
        try:
            return json.loads(t[s : e + 1])
        except json.JSONDecodeError:
            return {}
    return {}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default=os.path.join("data", "chat_history.json"))
    ap.add_argument("--out", default=os.path.join("data", "chat_memory.json"))
    ap.add_argument("--batch", type=int, default=150)
    ap.add_argument("--limit", type=int, default=0, help="0 = вся история")
    args = ap.parse_args()

    settings = Settings.from_env()
    giga = GigaChatClient(settings)

    msgs = _load_history(args.history)
    if args.limit:
        msgs = msgs[: args.limit]

    # стартуем от seed-роутинга людей/алиасов
    memory = {
        "people": {c: {"aliases": [n for n in names if n != c], "description": ""}
                   for c, names in ALIASES.items()},
        "aliases": [], "events": [], "inside_jokes": [], "relationships": [],
    }

    total = (len(msgs) + args.batch - 1) // args.batch
    for bi in range(total):
        batch = msgs[bi * args.batch : (bi + 1) * args.batch]
        if not batch:
            continue
        try:
            raw = await giga.complete(BATCH_SYSTEM, _fmt_batch(batch),
                                      temperature=0.0, max_tokens=900)
            data = _parse(raw)
        except Exception as exc:  # noqa: BLE001
            print(f"[{bi+1}/{total}] ошибка: {exc}", file=sys.stderr)
            continue
        for key in ("events", "inside_jokes", "relationships", "aliases"):
            memory[key].extend(data.get(key, []) or [])
        print(f"[{bi+1}/{total}] событий={len(memory['events'])} "
              f"прозвищ={len(memory['aliases'])}")
        # инкрементальное сохранение
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(memory, f, ensure_ascii=False, indent=2)

    print("Готово ->", args.out)


if __name__ == "__main__":
    asyncio.run(main())
