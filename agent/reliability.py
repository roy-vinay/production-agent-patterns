"""Retries with backoff and jitter, and budgets that stop a runaway loop."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .backends import ServiceUnavailable

RETRYABLE = (ServiceUnavailable, TimeoutError, ConnectionError)


def call_with_retry(fn, *args, attempts: int = 3, base_delay: float = 1.0, sleep=time.sleep, **kwargs):
    """Retry only errors that can fix themselves. Waits 1s, 2s, 4s plus jitter."""
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except RETRYABLE:
            if i == attempts - 1:
                raise
            sleep(base_delay * 2 ** i + random.uniform(0, 0.5))


@dataclass
class Budget:
    """Three budgets, because each catches a different failure."""
    max_steps: int = 8
    max_seconds: float = 30.0
    max_tokens: int = 40_000
    steps: int = 0
    tokens: int = 0
    started: float = field(default_factory=time.monotonic)

    def charge(self, tokens: int) -> None:
        self.steps += 1
        self.tokens += tokens

    def exceeded(self) -> str | None:
        if self.steps >= self.max_steps:
            return "step budget"
        if self.tokens >= self.max_tokens:
            return "token budget"
        if time.monotonic() - self.started >= self.max_seconds:
            return "time budget"
        return None
