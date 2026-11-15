"""Optional integration with Laya, an open-source (Apache-2.0) typed-decision model.

Laya (https://github.com/NandhaKishorM/laya) answers ``score`` questions over an ordinal scale with
calibrated probabilities. We run it as a separate service (``laya-serve``) and ask it to rate the
expected crowding of each ride from a structured description of the context; its answer is blended
with the rule-based estimate. When the service is not configured or not reachable, only the
rule-based model is used.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)

CROWD_LEVELS = [
    "empty: plenty of free seats, nobody around",
    "quiet: some free seats, a few people",
    "moderate: seats taken, people standing with space",
    "crowded: many people standing close together",
    "packed: no room, people pressed together",
]

CROWD_QUESTION: dict[str, Any] = {
    "crowd": {
        "type": "score",
        "instructions": (
            "How crowded will this public transport ride be for a passenger who wants to avoid crowds? "
            "Consider rush hours, large events nearby, main stations, the city centre and busy lines."
        ),
        "criteria": CROWD_LEVELS,
    }
}

MAX_BATCH = 64
_FAILURE_BACKOFF_S = 60.0


class LayaClient:
    def __init__(self) -> None:
        self._down_until = 0.0
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(get_settings().laya_url)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        s = get_settings()
        headers = {"Content-Type": "application/json"}
        if s.laya_api_key:
            headers["Authorization"] = f"Bearer {s.laya_api_key}"
        with httpx.Client(timeout=s.laya_timeout_seconds) as client:
            response = client.post(f"{s.laya_url.rstrip('/')}/v1/systemone/batch", json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    def score_crowd(self, states: list[dict[str, Any]]) -> list[float | None]:
        """Return a 0..1 crowd estimate per state, or ``None`` where Laya could not answer."""
        results: list[float | None] = [None] * len(states)
        if not states or not self.enabled:
            return results
        with self._lock:
            if time.monotonic() < self._down_until:
                return results
        s = get_settings()
        try:
            for start in range(0, len(states), MAX_BATCH):
                chunk = states[start : start + MAX_BATCH]
                # Bound tensor size for CPU serving without reducing the HTTP batch.
                payload: dict[str, Any] = {"states": chunk, "questions": CROWD_QUESTION, "batch_size": 4}
                if s.laya_model:
                    payload["model"] = s.laya_model
                data = self._post(payload)
                for offset, result in enumerate(data.get("results", [])[: len(chunk)]):
                    results[start + offset] = parse_crowd_answer(result)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            log.warning("Laya decision model unavailable, using rule-based model only: %s", exc)
            with self._lock:
                self._down_until = time.monotonic() + _FAILURE_BACKOFF_S
        return results


def parse_crowd_answer(result: dict[str, Any]) -> float | None:
    answer = (result or {}).get("answers", {}).get("crowd")
    if not answer:
        return None
    score = answer.get("score")
    if score is None:
        probabilities = answer.get("probabilities") or {}
        if not probabilities:
            return None
        # expected level from the distribution (keys are level labels or indices)
        total = 0.0
        for key, p in probabilities.items():
            level = CROWD_LEVELS.index(key) if key in CROWD_LEVELS else int(key)
            total += level * float(p)
        score = total
    return max(0.0, min(1.0, float(score) / (len(CROWD_LEVELS) - 1)))


laya_client = LayaClient()
