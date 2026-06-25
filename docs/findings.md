# Mini Economic Index — findings from a reproduction run

A short, engineer's-eye write-up of one run of the [`adp.econ`](../adp/econ/) module:
a small, offline-reproducible replica of the **Anthropic Economic Index** methodology.
It classifies real human–LLM conversations into occupational tasks and an
**automation-vs-augmentation** label, then checks the resulting distribution against a
reference. This is a *data-platform* artifact — the point is the reproducible pipeline,
not original economic research.

> **TL;DR** — On 150 real conversations from WildChat-1M, classified locally by `qwen2.5:7b`,
> usage concentrates in **writing, software and data analysis (~71% combined)** — the same
> shape Anthropic's published Economic Index reports — and is **augmentation-heavy overall
> (69% augmentation vs 31% automation)**. Software is the outlier: people mostly ask the model
> to *do* it (78% automation), whereas writing and data skew toward *help me* (augmentation).

## Method

```
WildChat-1M (real conversations)  →  classify each: occupation (O*NET-style) + automation|augmentation
                                  →  aggregate to occupation shares
                                  →  validate the distribution vs a reference (Spearman)
```

- **Corpus:** [WildChat-1M](https://huggingface.co/datasets/allenai/WildChat-1M) (`allenai/WildChat-1M`, ODC-BY) — used for analysis only.
- **Classifier:** `qwen2.5:7b` running locally via **Ollama** (free, on-GPU), temperature 0.
- **Run:** n = 150 conversations, 0 backend fallbacks, ~7 min. Fully reproducible (see below).

## Result

![AI usage by occupation](econ_index.svg)

| Occupation | Share | Automation | n |
|---|---|---|---|
| Writing & Content | 30.7% | 24% | 46 |
| Software & IT | 21.3% | 78% | 32 |
| Data & Analysis | 18.7% | 21% | 28 |
| Personal & Other | 9.3% | 14% | 14 |
| Finance & Accounting | 6.0% | 22% | 9 |
| Translation & Languages | 4.7% | 14% | 7 |
| Science & Research | 2.7% | 0% | 4 |
| Marketing & Sales | 1.3% | 0% | 2 |
| Legal | 1.3% | 0% | 2 |
| Healthcare & Wellness | 1.3% | 0% | 2 |
| Design & Creative | 1.3% | 0% | 2 |
| Education & Tutoring | 0.7% | 0% | 1 |
| Management & Business Ops | 0.7% | 0% | 1 |

**Overall: automation 31% · augmentation 69%.**

## What it shows

1. **Coding + writing + data lead** (~71% together) — consistent with the *shape* of the published Anthropic Economic Index, which is the basic sanity check this exercise is for.
2. **Augmentation > automation** (69/31) — most requests are "help me do X," not "do X for me."
3. **Software is the automation outlier** (78%) — "just write the code"; writing/data skew the other way ("explain", "review", "how do I").

## Validation

Spearman rank correlation against a reference distribution: **ρ = +0.32**, top-5 occupation
overlap **3/5**. The ranking *broadly* agrees but is not tight — exactly what you'd expect at
this sample size and classifier strength, and a useful honesty signal that the pipeline isn't
overfit to its reference (the offline heuristic-on-templated-samples demo scores ρ ≈ +0.92 by
construction; real messy data correctly drops it).

## Limitations (read these)

- **Proxy data.** WildChat is *ChatGPT* traffic — **not Claude usage**. Findings are illustrative of the *method*, not claims about Claude or the real economy.
- **Small sample (n=150).** Long-tail categories (n = 1–2) are noise; don't read into them.
- **Local 7B classifier.** Lower accuracy than a frontier model; there is **no hand-labeled gold set** yet, so no per-class precision/recall or inter-annotator agreement.
- **Coarse reference.** Validation is against a hand-made stand-in, not Anthropic's published CC-BY `EconomicIndex` release.
- **Descriptive only — no causal claims.**

## Reproduce

```bash
uv run adp econ --source wildchat --backend ollama --n 150   # this run (needs a local Ollama model)
uv run adp econ                                               # fully offline heuristic demo (free, no model)
```

## What I'd build next (to make it real)

- Scale to a **stratified 20–50k sample**; bulk-classify on a cheap/local model, spot-check on a frontier model, and report cost-per-N.
- Swap in the **full O*NET task database** and validate against Anthropic's published **`EconomicIndex`** (CC-BY) instead of a hand-made reference.
- Add a **hand-labeled gold set** + classifier eval (per-class F1, Cohen's κ, calibration).
- **Privacy-preserving aggregation** (k-anonymity / suppression) before anything is published.
