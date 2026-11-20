"""Score the tool against the ground-truth dataset.

For every case the tool is given what a pull request would contain: the project before and after
the mutation. It finds the changes (`diff_projects`), the impacts (`compute_impact`) and predicts
for each model that the mutation is judged on:

- breaking       some impact on the model is breaking
- needs_review   impacts, but none is breaking
- none           no impact

The prediction is compared with what dbt really did (breaking, silent or safe). This scores the
deterministic layer only; a judge for `needs_review` impacts is scored separately.

Every ratio keeps its numerator and denominator, so a number resting on a few examples shows it.
"""

from collections import Counter
from dataclasses import dataclass, field

from schema_impact.core.changes import diff_projects
from schema_impact.core.impact import BREAKING, NEEDS_REVIEW, compute_impact
from schema_impact.core.registry import Project
from schema_impact.evals.dataset import Dataset

PREDICTED_BREAKING = "breaking"
PREDICTED_REVIEW = "needs_review"
PREDICTED_NONE = "none"

_PREDICTIONS = (PREDICTED_BREAKING, PREDICTED_REVIEW, PREDICTED_NONE)
_TRUTHS = ("breaking", "silent", "safe")


@dataclass(frozen=True)
class Outcome:
    """The tool's prediction and the truth for one model in one case."""

    case_id: str
    kind: str  # the kind of mutation
    model: str
    truth: str
    prediction: str


@dataclass(frozen=True)
class ScoreResult:
    outcomes: list[Outcome]
    skipped: list[str]  # ids of cases that could not be scored (dbt had ended in an error)


@dataclass(frozen=True)
class Ratio:
    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        """None when there is nothing to divide by, which is not the same as 0."""
        return self.numerator / self.denominator if self.denominator else None

    def __str__(self) -> str:
        shown = "n/a" if self.value is None else f"{self.value:.1%}"
        return f"{shown} ({self.numerator}/{self.denominator})"


@dataclass(frozen=True)
class Metrics:
    pairs: int
    affected_precision: Ratio  # of the models flagged (breaking or needs_review), how many were hit
    affected_recall: Ratio  # of the models hit (breaking or silent), how many were flagged
    affected_f1: float | None
    breaking_precision: Ratio  # only a "breaking" prediction counts here
    breaking_recall: Ratio
    silent_flagged: Ratio  # of the silent models, how many got any prediction but none
    review_breakdown: dict[str, int]  # what the needs_review predictions turned out to be
    confusion: dict[tuple[str, str], int] = field(default_factory=dict)  # (truth, prediction)


# --- predicting ---------------------------------------------------------------


def predict_case(before: Project, after: Project, models: list[str]) -> dict[str, str]:
    """Predict, for each of `models`, how a change from `before` to `after` affects it."""
    impacts = compute_impact(before, diff_projects(before, after))
    worst: dict[str, str] = {}
    for impact in impacts:
        if impact.effect == BREAKING:
            worst[impact.model] = PREDICTED_BREAKING
        elif impact.effect == NEEDS_REVIEW and worst.get(impact.model) != PREDICTED_BREAKING:
            worst[impact.model] = PREDICTED_REVIEW
    return {model: worst.get(model, PREDICTED_NONE) for model in models}


def score_dataset(dataset: Dataset) -> ScoreResult:
    """The outcome of every scored model of every case."""
    outcomes: list[Outcome] = []
    skipped: list[str] = []
    for case in dataset.cases:
        if case.error or case.after is None:
            skipped.append(case.mutation.id)
            continue
        predictions = predict_case(dataset.baseline_project, case.after, list(case.labels))
        outcomes += [
            Outcome(case.mutation.id, case.mutation.kind, model, truth, predictions[model])
            for model, truth in case.labels.items()
        ]
    return ScoreResult(outcomes, skipped)


# --- metrics ------------------------------------------------------------------


def compute_metrics(outcomes: list[Outcome]) -> Metrics:
    confusion = Counter((o.truth, o.prediction) for o in outcomes)

    def count(truths: tuple[str, ...], predictions: tuple[str, ...]) -> int:
        return sum(n for (t, p), n in confusion.items() if t in truths and p in predictions)

    hit = ("breaking", "silent")
    flagged = (PREDICTED_BREAKING, PREDICTED_REVIEW)

    affected_precision = Ratio(count(hit, flagged), count(_TRUTHS, flagged))
    affected_recall = Ratio(count(hit, flagged), count(hit, _PREDICTIONS))
    p, r = affected_precision.value, affected_recall.value
    f1 = 2 * p * r / (p + r) if p is not None and r is not None and p + r > 0 else None

    review = Counter({t: n for (t, p_), n in confusion.items() if p_ == PREDICTED_REVIEW})
    return Metrics(
        pairs=len(outcomes),
        affected_precision=affected_precision,
        affected_recall=affected_recall,
        affected_f1=f1,
        breaking_precision=Ratio(
            count(("breaking",), (PREDICTED_BREAKING,)), count(_TRUTHS, (PREDICTED_BREAKING,))
        ),
        breaking_recall=Ratio(
            count(("breaking",), (PREDICTED_BREAKING,)), count(("breaking",), _PREDICTIONS)
        ),
        silent_flagged=Ratio(count(("silent",), flagged), count(("silent",), _PREDICTIONS)),
        review_breakdown=dict(review),
        confusion=dict(confusion),
    )


def metrics_by_kind(outcomes: list[Outcome]) -> dict[str, Metrics]:
    kinds = sorted({o.kind for o in outcomes})
    return {kind: compute_metrics([o for o in outcomes if o.kind == kind]) for kind in kinds}


# --- reporting ----------------------------------------------------------------


def format_report(metrics: Metrics) -> str:
    f1 = "n/a" if metrics.affected_f1 is None else f"{metrics.affected_f1:.1%}"
    rows = [
        ("Affected models: precision", metrics.affected_precision),
        ("Affected models: recall", metrics.affected_recall),
        ("Affected models: F1", f1),
        ("Breaking: precision", metrics.breaking_precision),
        ("Breaking: recall", metrics.breaking_recall),
        ("Silent models flagged (anything but none)", metrics.silent_flagged),
    ]
    lines = ["| Metric | Value |", "|---|---|"] + [f"| {name} | {value} |" for name, value in rows]
    if metrics.review_breakdown:
        total = sum(metrics.review_breakdown.values())
        parts = ", ".join(f"{n} {truth}" for truth, n in sorted(metrics.review_breakdown.items()))
        lines.append(f"| needs_review turned out to be | {parts} (of {total}) |")
    return "\n".join(lines)


def format_confusion(metrics: Metrics) -> str:
    header = "| truth \\ predicted | " + " | ".join(_PREDICTIONS) + " |"
    lines = [header, "|---|" + "---|" * len(_PREDICTIONS)]
    for truth in _TRUTHS:
        cells = " | ".join(str(metrics.confusion.get((truth, p), 0)) for p in _PREDICTIONS)
        lines.append(f"| {truth} | {cells} |")
    return "\n".join(lines)


def format_by_kind(by_kind: dict[str, Metrics]) -> str:
    lines = [
        "| Mutation kind | Pairs | Affected recall | Breaking recall | Silent flagged |",
        "|---|---|---|---|---|",
    ]
    for kind, m in by_kind.items():
        cells = [m.pairs, m.affected_recall, m.breaking_recall, m.silent_flagged]
        lines.append(f"| {kind} | " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(lines)


def format_errors(outcomes: list[Outcome]) -> str:
    """The cases and models the tool got wrong: misses, and false alarms on safe models."""
    missed = [o for o in outcomes if o.truth in ("breaking", "silent") and o.prediction == "none"]
    alarms = [o for o in outcomes if o.truth == "safe" and o.prediction != "none"]
    sections = [
        ("Missed (breaking or silent, predicted none)", missed),
        ("False alarms (safe, but flagged)", alarms),
    ]
    lines: list[str] = []
    for title, wrong in sections:
        lines += [f"### {title}: {len(wrong)}", ""]
        by_case: dict[str, list[str]] = {}
        for o in wrong:
            by_case.setdefault(o.case_id, []).append(o.model)
        lines += [
            f"- {case}: {', '.join(sorted(models))}" for case, models in sorted(by_case.items())
        ]
        if not wrong:
            lines.append("- none")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_report(dataset: Dataset, result: ScoreResult, title: str) -> str:
    """A markdown report of one scoring run."""
    metrics = compute_metrics(result.outcomes)
    meta = dataset.meta
    sources = f"dbt-core {meta.get('dbt_core')}, dbt-duckdb {meta.get('dbt_duckdb')}"
    lines = [
        f"# {title}",
        "",
        f"{len(dataset.cases) - len(result.skipped)} cases, {metrics.pairs} (case, model) pairs. "
        f"{len(result.skipped)} cases skipped. Ground truth from {sources}.",
        "",
        "## Metrics",
        "",
        format_report(metrics),
        "",
        "## Confusion matrix",
        "",
        format_confusion(metrics),
        "",
        "## By mutation kind",
        "",
        format_by_kind(metrics_by_kind(result.outcomes)),
        "",
        "## Errors",
        "",
        format_errors(result.outcomes),
        "",
    ]
    return "\n".join(lines)
