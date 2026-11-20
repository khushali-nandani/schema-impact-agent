"""The judge: the one place where an LLM is allowed to decide something.

The core layer is deterministic. It can say that a change *needs review* but not how risky it
is, because that depends on what the numbers mean. A Judge looks at one impact and answers:
how risky is it, why, and what should be done.

`Judge` is a small interface so the model behind it can be swapped (a local Ollama model, a
hosted API, or the `FakeJudge` used in tests).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from schema_impact.core.impact import Impact

HIGH = "high"
LOW = "low"


@dataclass(frozen=True)
class Judgment:
    risk: str  # HIGH or LOW
    explanation: str  # why, in plain language
    fix: str  # what to do about it


@dataclass(frozen=True)
class Verdict:
    """A judgment together with the impact it is about."""

    impact: Impact
    judgment: Judgment


class Judge(Protocol):
    def judge(self, impact: Impact) -> Judgment: ...


class FakeJudge:
    """A judge for tests: returns a fixed judgment, or one computed from the impact."""

    def __init__(self, response: Judgment | Callable[[Impact], Judgment]):
        self._response = response
        self.calls: list[Impact] = []

    def judge(self, impact: Impact) -> Judgment:
        self.calls.append(impact)
        if callable(self._response):
            return self._response(impact)
        return self._response
