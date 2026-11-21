# Benchmark results

Each file is the output of `python -m schema_impact.evals.score` on `../dataset/jaffle_shop.json`, saved at one state of the code, so that the effect of every fix can be seen. Files are dated.

| File | State of the code |
|---|---|
| `2026-11-20-baseline-deterministic.md` | L0 + L1, before any fix |
| `2026-11-20-after-fix-a.md` | a breaking impact takes the whole model down |
| `2026-11-20-after-fix-b.md` | a model's own output columns are compared |
| `2026-11-20-after-fix-d.md` | clause columns are traced through CTEs |
| `2026-11-20-after-fix-c1.md` | an edit that only changes the output shape is not a model change |
| `2026-11-20-final-with-judge.md` | the same code, plus a local LLM judge on the needs_review pairs |
| `2026-11-20-llm-judgments.json` | the judge's 93 answers (qwen2.5-coder:7b), so the last report can be rebuilt without a model |

The fixes were chosen by reading the errors of this dataset, so the later rows are not held-out estimates. See [docs/architecture.md](../../docs/architecture.md#benchmark-l3).

## Olist

| File | State of the code |
|---|---|
| `2026-11-21-olist-held-out.md` | the code of v0.4.0, scored once on `../dataset/olist.json` before any change. The report was committed before it was read, so this is the held-out result. Later Olist files, if any, are tuned on its errors and say so. |
| `2026-11-21-olist-after-text-types.md` | fix 1: a column that becomes text breaks the models that sum, divide or `date_diff` it, and everything that reads them. **Tuned** on the errors of the held-out run, so not a held-out estimate. |
| `2026-11-21-jaffle-after-text-types.md` | the same code on jaffle_shop (also not held-out): the 3 breaking pairs that stayed `needs_review` are now breaking. |
| `2026-11-21-olist-after-column-logic.md` | fix 2: an edit that only changes how some output columns are computed is reported per column (`ColumnLogicChanged`), so only the models that read those columns are flagged. **Tuned** on the errors of the held-out run: false alarms 59 → 16, nothing missed, breaking precision and recall 100%. |
| `2026-11-21-jaffle-after-column-logic.md` | the same code on jaffle_shop (also not held-out): false alarms 7 → 5, nothing missed. |
