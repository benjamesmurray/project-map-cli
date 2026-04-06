# utils/digest_tool_v2/common/order.py
from __future__ import annotations

from typing import Any, Callable, Iterable, List, Mapping, MutableMapping, Sequence, Tuple


# --------- key builders (deterministic, stable) ---------

def lexi(*fields: str) -> Callable[[Mapping[str, Any]], Tuple]:
    """
    Build a lexicographic key over dict fields (ascending for all).
    Missing fields are treated as empty string; non-scalars are stringified.

    Example:
        items.sort(key=lexi("module", "name"))
    """
    def _key(d: Mapping[str, Any]) -> Tuple:
        out = []
        for f in fields:
            v = d.get(f, "")
            # Normalize None and complex values to stable strings
            if v is None:
                v = ""
            elif not isinstance(v, (str, int, float, bool)):
                v = str(v)
            out.append(v)
        return tuple(out)
    return _key


def rank_then_lexi(primary_field: str,
                   *,
                   primary_desc: bool = True,
                   tie_breakers: Sequence[str] = ()) -> Callable[[Mapping[str, Any]], Tuple]:
    """
    Sort by a numeric 'primary_field' (desc by default), then lexicographically by tie_breakers.

    Example:
        items.sort(key=rank_then_lexi("score", primary_desc=True, tie_breakers=("module","name")))
    """
    def _key(d: Mapping[str, Any]) -> Tuple:
        p = d.get(primary_field, 0)
        if not isinstance(p, (int, float)):
            # Non-numeric primary ranks as 0
            p = 0
        # Use negative for descending without relying on reverse=True
        primary = -p if primary_desc else p
        tail = []
        for f in tie_breakers:
            v = d.get(f, "")
            if v is None:
                v = ""
            elif not isinstance(v, (str, int, float, bool)):
                v = str(v)
            tail.append(v)
        return (primary, *tail)
    return _key


# --------- helpers to return sorted copies ---------

def sorted_items_by_key(d: Mapping[str, Any]) -> List[Tuple[str, Any]]:
    """
    Deterministic items() sorted by key (as string).
    """
    return sorted(d.items(), key=lambda kv: str(kv[0]))


def sort_inplace_list_of_dicts(items: List[MutableMapping[str, Any]],
                               key: Callable[[Mapping[str, Any]], Tuple]) -> List[MutableMapping[str, Any]]:
    """
    Sort a list of dicts in-place using the provided key, return the list for chaining.
    """
    items.sort(key=key)
    return items


def sorted_list_of_dicts(items: Iterable[Mapping[str, Any]],
                         key: Callable[[Mapping[str, Any]], Tuple]) -> List[Mapping[str, Any]]:
    """
    Return a new list sorted using the provided key.
    """
    out = list(items)
    out.sort(key=key)
    return out


def normalize_bool_int(x: Any) -> int:
    """
    Useful for consistent ranking when values may be bools.
    True > False deterministically maps to 1 > 0.
    Non-bools fall back to int(x) if possible, else 0.
    """
    if isinstance(x, bool):
        return 1 if x else 0
    try:
        return int(x)
    except Exception:
        return 0
