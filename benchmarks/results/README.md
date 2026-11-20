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
