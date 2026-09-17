"""Company blacklist. Exact normalized name match only; not industry/title semantics."""

from __future__ import annotations

import re
from typing import Iterable

_SPACE_RE = re.compile(r"\s+")
_COMPANY_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "有限公司",
    "集团",
    "公司",
)


def normalize_company(name: str | None) -> str | None:
    if name is None:
        return None
    text = _SPACE_RE.sub("", str(name)).strip().casefold()
    if not text:
        return None
    for suffix in _COMPANY_SUFFIXES:
        folded = suffix.casefold()
        if text.endswith(folded) and len(text) > len(folded):
            text = text[: -len(folded)]
            break
    return text or None


def company_is_blacklisted(company_name: str | None, blacklist: Iterable[str] | None) -> bool:
    target = normalize_company(company_name)
    if not target:
        return False
    for item in blacklist or []:
        listed = normalize_company(item)
        if listed and listed == target:
            return True
    return False
