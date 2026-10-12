"""A few whole-sentence patterns. A hit means confidence 1.

The pattern has to match the entire utterance. A policy id buried in a longer
request is not certain enough for this layer.
"""

from __future__ import annotations

import re

_POLICY_ID = re.compile(
    r"^\s*(?:请)?(?:查阅|查看|打开)?\s*制度编号\s*[:：]?\s*[A-Za-z]{2,8}-\d{2,6}(?:-\d{2,6})?\s*$"
)


def policy_lookup(text: str) -> str | None:
    if _POLICY_ID.match(text):
        return "policy_lookup"
    return None
