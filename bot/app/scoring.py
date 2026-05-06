from __future__ import annotations

import re

from app.models import Subscription
from app.sources.base import Listing


_HOT_WORDS = {
    "срочно": 8,
    "торг": 3,
    "дёшево": 6,
    "дешево": 6,
    "ниже рынка": 8,
    "новый": 2,
    "запечат": 2,  # "запечатан"
}


def deal_score(sub: Subscription, item: Listing) -> float:
    """
    Very simple heuristics:
    - cheaper within the subscription price range -> higher
    - keywords in title -> higher
    """
    score = 0.0
    title = (item.title or "").lower()

    for kw, w in _HOT_WORDS.items():
        if kw in title:
            score += float(w)

    if item.price is not None:
        if sub.price_max is not None and sub.price_max > 0:
            # normalized: lower price -> higher score
            score += max(0.0, (sub.price_max - item.price) / sub.price_max) * 10.0
        elif sub.price_min is not None and sub.price_min > 0:
            score += max(0.0, (sub.price_min - item.price) / sub.price_min) * 3.0

        # prefer round-ish cheap numbers (weak signal)
        if re.search(r"(000|999)\b", str(item.price)):
            score += 0.5

    return score

