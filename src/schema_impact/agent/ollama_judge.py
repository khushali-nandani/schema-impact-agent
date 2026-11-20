"""A judge backed by a local Ollama model.

The model is only asked what the deterministic analysis could not decide: when a change does
not make a query fail, could the numbers silently become wrong? It gets the facts of one
impact and must answer in JSON (risk, explanation, fix). The answer is validated; an answer
that cannot be understood is retried with a correction, and if it still cannot be understood
the judge falls back to HIGH risk, so a person looks at it. Guessing "low" would hide problems.

A connection problem (Ollama not running) is not hidden: the error is raised.
"""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from schema_impact.agent.describe import (
    describe_change,
    describe_path,
    describe_place,
    describe_roles,
)
from schema_impact.agent.judge import HIGH, Judgment
from schema_impact.core.impact import Impact

DEFAULT_MODEL = "qwen2.5-coder:7b"

SYSTEM_PROMPT = (
    "You review changes to a SQL data pipeline. A deterministic analysis has found one place "
    "that depends on something that changed, and it cannot tell whether that matters. Decide "
    "whether the numbers produced there could silently become wrong or misleading (risk "
    '"high") or whether the effect is harmless (risk "low"). Use only the facts you are given; '
    "do not invent tables, columns or values. Answer with JSON only."
)

CORRECTION = (
    "That was not valid. Answer again with only JSON of the form "
    '{"risk": "high" or "low", "explanation": "...", "fix": "..."}.'
)


class _Answer(BaseModel):
    risk: Literal["high", "low"]
    explanation: str
    fix: str


class _Client(Protocol):
    def chat(self, **kwargs: Any) -> Any: ...


class OllamaJudge:
    def __init__(
        self,
        client: _Client | None = None,
        model: str = DEFAULT_MODEL,
        model_sql: dict[str, str] | None = None,
        retries: int = 2,
    ):
        """
        client:    an ollama.Client; created on first use if not given
        model_sql: SQL of each model by name, shown to the model so it can see how the
                   changed column is used
        retries:   extra attempts after an answer that cannot be understood
        """
        self._client = client
        self.model = model
        self.model_sql = model_sql or {}
        self.retries = retries

    def judge(self, impact: Impact) -> Judgment:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._facts(impact)},
        ]
        for _ in range(self.retries + 1):
            content = self._ask(messages)
            parsed = _parse(content)
            if parsed is not None:
                return Judgment(risk=parsed.risk, explanation=parsed.explanation, fix=parsed.fix)
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": CORRECTION},
            ]
        return Judgment(
            risk=HIGH,
            explanation=(
                "The model's answer could not be understood, so this impact has not been "
                "assessed. A person should review it."
            ),
            fix=f"Review manually: {describe_change(impact.change)}.",
        )

    def _ask(self, messages: list[dict[str, str]]) -> str:
        response = self._get_client().chat(
            model=self.model,
            messages=messages,
            format=_Answer.model_json_schema(),
            options={"temperature": 0},
        )
        return response.message.content or ""

    def _get_client(self) -> _Client:
        if self._client is None:
            import ollama

            self._client = ollama.Client()
        return self._client

    def _facts(self, impact: Impact) -> str:
        lines = [
            f"Change: {describe_change(impact.change)}",
            f"Affected: {describe_place(impact)}",
            f"Used as: {describe_roles(impact)}",
            f"Path: {describe_path(impact)}",
            f"Found by the analysis: {impact.effect}",
        ]
        sql = self.model_sql.get(impact.model)
        if sql:
            lines += ["", f"SQL of {impact.model}:", sql.strip()]
        lines += [
            "",
            'Answer with JSON: "risk" ("high" or "low"), "explanation" (one or two '
            'sentences), "fix" (one sentence).',
        ]
        return "\n".join(lines)


def _parse(content: str) -> _Answer | None:
    try:
        return _Answer.model_validate_json(content)
    except ValidationError:
        return None
