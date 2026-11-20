"""Score an LLM judge on the impacts the deterministic layer could not decide.

The deterministic layer predicts "needs_review" for a model when something it reads changed but
nothing certainly breaks (a type change, a changed definition). A judge is asked about each such
impact and answers high or low. For scoring, a model stays flagged if any of its impacts was
judged high and is dropped if all were judged low. Models predicted breaking, or not reached at
all, are never put to the judge.

The judge is slow, so its answers are kept in a file; scoring reads that file and needs no model.
A model the judge was not asked about stays flagged, which is the careful choice.
"""

import json
import os
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from schema_impact.agent.describe import describe_change
from schema_impact.agent.judge import HIGH, LOW, Judge
from schema_impact.core.changes import diff_projects
from schema_impact.core.impact import BREAKING, NEEDS_REVIEW, Impact, compute_impact
from schema_impact.evals.dataset import Dataset
from schema_impact.evals.scorer import PREDICTED_NONE, PREDICTED_REVIEW, Metrics, Outcome

_TRUTHS = ("breaking", "silent", "safe")


@dataclass(frozen=True)
class ReviewItem:
    """One impact to put to the judge, with the truth about its model."""

    case_id: str
    model: str
    truth: str
    impact: Impact


@dataclass(frozen=True)
class JudgedItem:
    key: str
    case_id: str
    model: str
    column: str | None
    roles: tuple[str, ...]
    change: str
    risk: str
    explanation: str
    fix: str


def item_key(case_id: str, impact: Impact) -> str:
    """Identifies an impact within a dataset, so an answer can be matched to it again."""
    roles = ",".join(sorted(impact.roles))
    return f"{case_id}|{impact.model}|{impact.column}|{roles}|{describe_change(impact.change)}"


def collect_review_items(dataset: Dataset) -> list[ReviewItem]:
    """The impacts of every model the deterministic layer predicts needs_review."""
    before = dataset.baseline_project
    items: list[ReviewItem] = []
    for case in dataset.cases:
        if case.error or case.after is None:
            continue
        impacts = compute_impact(before, diff_projects(before, case.after))
        for model, truth in case.labels.items():
            own = [impact for impact in impacts if impact.model == model]
            if not own or any(impact.effect == BREAKING for impact in own):
                continue
            items += [
                ReviewItem(case.mutation.id, model, truth, impact)
                for impact in own
                if impact.effect == NEEDS_REVIEW
            ]
    return items


def judge_items(
    items: Sequence[ReviewItem],
    judge: Judge,
    already: Sequence[JudgedItem] = (),
    progress: Callable[[str], None] = print,
    on_item: Callable[[list[JudgedItem]], None] | None = None,
) -> list[JudgedItem]:
    """Ask the judge about each item that has no answer yet. Answers come back in item order."""
    done = {judged.key: judged for judged in already}
    result: list[JudgedItem] = []
    for number, item in enumerate(items, start=1):
        key = item_key(item.case_id, item.impact)
        if key not in done:
            started = time.monotonic()
            judgment = judge.judge(item.impact)
            done[key] = JudgedItem(
                key=key,
                case_id=item.case_id,
                model=item.model,
                column=item.impact.column,
                roles=tuple(sorted(item.impact.roles)),
                change=describe_change(item.impact.change),
                risk=judgment.risk,
                explanation=judgment.explanation,
                fix=judgment.fix,
            )
            progress(
                f"[{number}/{len(items)}] {item.case_id} / {item.model}: {judgment.risk} "
                f"({time.monotonic() - started:.1f}s)"
            )
            if on_item:
                on_item(list(done.values()))
        result.append(done[key])
    return result


# --- the file of answers -------------------------------------------------------


def save_judgments(path: str | Path, judged: Sequence[JudgedItem], judge_model: str) -> None:
    path = Path(path)
    data = {
        "meta": {
            "judge_model": judge_model,
            "generated": date.today().isoformat(),
            "items": len(judged),
        },
        "items": [{**item.__dict__, "roles": list(item.roles)} for item in judged],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=1) + "\n")
    os.replace(temporary, path)


def load_judgments(path: str | Path) -> tuple[dict[str, Any], list[JudgedItem]]:
    data = json.loads(Path(path).read_text())
    items = [JudgedItem(**{**item, "roles": tuple(item["roles"])}) for item in data["items"]]
    return data["meta"], items


# --- scoring --------------------------------------------------------------------


def _verdicts(judged: Sequence[JudgedItem]) -> dict[tuple[str, str], str]:
    """high if any impact of the model was judged high, else low."""
    verdicts: dict[tuple[str, str], str] = {}
    for item in judged:
        pair = (item.case_id, item.model)
        verdicts[pair] = HIGH if HIGH in (verdicts.get(pair), item.risk) else LOW
    return verdicts


def apply_judgments(outcomes: list[Outcome], judged: Sequence[JudgedItem]) -> list[Outcome]:
    """The predictions after the judge: needs_review pairs it judged low become none."""
    verdicts = _verdicts(judged)
    return [
        replace(o, prediction=PREDICTED_NONE)
        if o.prediction == PREDICTED_REVIEW and verdicts.get((o.case_id, o.model)) == LOW
        else o
        for o in outcomes
    ]


def judge_accuracy(
    outcomes: list[Outcome], judged: Sequence[JudgedItem]
) -> dict[tuple[str, str], int]:
    """For the needs_review pairs the judge was asked about: (truth, judge's answer) -> count."""
    verdicts = _verdicts(judged)
    counts: Counter[tuple[str, str]] = Counter()
    for o in outcomes:
        verdict = verdicts.get((o.case_id, o.model))
        if o.prediction == PREDICTED_REVIEW and verdict is not None:
            counts[(o.truth, verdict)] += 1
    return dict(counts)


# --- reporting ------------------------------------------------------------------


def format_comparison(
    deterministic: Metrics, with_judge: Metrics, accuracy: dict[tuple[str, str], int]
) -> str:
    def f1(m: Metrics) -> str:
        return "n/a" if m.affected_f1 is None else f"{m.affected_f1:.1%}"

    rows = [
        (
            "Affected models: precision",
            deterministic.affected_precision,
            with_judge.affected_precision,
        ),
        ("Affected models: recall", deterministic.affected_recall, with_judge.affected_recall),
        ("Affected models: F1", f1(deterministic), f1(with_judge)),
        ("Breaking: precision", deterministic.breaking_precision, with_judge.breaking_precision),
        ("Breaking: recall", deterministic.breaking_recall, with_judge.breaking_recall),
        ("Silent models flagged", deterministic.silent_flagged, with_judge.silent_flagged),
    ]
    lines = ["| Metric | Deterministic | With the judge |", "|---|---|---|"]
    lines += [f"| {name} | {a} | {b} |" for name, a, b in rows]
    lines += ["", "What the judge said about the models that needed review:", ""]
    lines += ["| truth \\ judge | high | low |", "|---|---|---|"]
    for truth in _TRUTHS:
        lines.append(
            f"| {truth} | {accuracy.get((truth, HIGH), 0)} | {accuracy.get((truth, LOW), 0)} |"
        )
    return "\n".join(lines)


def format_judge_errors(outcomes: list[Outcome], judged: Sequence[JudgedItem]) -> str:
    """The judge's wrong answers: a silent or breaking model judged low, a safe one judged high."""
    verdicts = _verdicts(judged)
    wrong = {"Judged low but not safe": [], "Judged high but safe": []}
    for o in outcomes:
        verdict = verdicts.get((o.case_id, o.model)) if o.prediction == PREDICTED_REVIEW else None
        if verdict == LOW and o.truth != "safe":
            wrong["Judged low but not safe"].append(f"- {o.case_id}: {o.model} ({o.truth})")
        elif verdict == HIGH and o.truth == "safe":
            wrong["Judged high but safe"].append(f"- {o.case_id}: {o.model}")
    lines: list[str] = []
    for title, entries in wrong.items():
        lines += [f"### {title}: {len(entries)}", ""] + (entries or ["- none"]) + [""]
    return "\n".join(lines).rstrip()
