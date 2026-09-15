"""Проверка ответов. Возвращает (correct, near) — near = «почти» (опечатка)."""
import difflib
import re
from typing import Any

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(s: Any) -> str:
    s = str(s if s is not None else "").lower().replace("ё", "е")
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _as_number(s: str) -> float | None:
    try:
        return float(s.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def check_text(given: str, card: dict) -> tuple[bool, bool]:
    candidates = [card.get("answer", "")] + list(card.get("accept") or [])
    raw = str(given).strip()
    g = normalize(given)
    if not g:
        return False, False
    gn = _as_number(raw)
    best = 0.0
    for c in candidates:
        num = _as_number(str(c).strip())
        if gn is not None and num is not None:
            if abs(gn - num) <= 1e-9 * max(1.0, abs(num)):
                return True, False
            continue
        cn = normalize(c)
        if g == cn:
            return True, False
        best = max(best, difflib.SequenceMatcher(None, g, cn).ratio())
    # длинные ответы с одной опечаткой считаем «почти»
    near = len(g) >= 4 and best >= 0.85
    return near, near


def check(card: dict, given: Any) -> dict:
    """given: index | list[int] | bool | str в зависимости от типа."""
    t = card["type"]
    correct, near = False, False
    if t == "choice":
        correct = given == card["answer"]
    elif t == "multi":
        correct = isinstance(given, list) and sorted(given) == sorted(card["answer"])
    elif t == "truefalse":
        correct = given == card["answer"]
    elif t == "text":
        correct, near = check_text(str(given), card)
    elif t == "flash":
        return {"correct": None, "near": False, "suggested": 3}
    # Again / Hard / Good
    suggested = 3 if correct and not near else (2 if near else 1)
    return {"correct": correct, "near": near, "suggested": suggested}
