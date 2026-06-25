# Economic Index module (`adp.econ`)

A small, offline-runnable replica of the **Anthropic Economic Index** methodology,
built on the platform. Pipeline:

```
conversations → classify (occupation + automation/augmentation)
             → raw.usage_classified  → aggregate → marts.econ_index
             → validate vs reference distribution (Spearman)
             → registered in catalog → served at GET /datasets/econ_index
```

Run it:

```bash
uv run adp econ                                  # free, offline (heuristic classifier)
ADP_ECON_CLASSIFIER=ollama uv run adp econ --n 500   # local LLM (free, uses your GPU)
```

📄 A write-up of a real run (WildChat-1M + local `qwen2.5:7b`) with a chart, validation and
limitations is in [`docs/findings.md`](../../docs/findings.md).

## Files

| file | role |
|---|---|
| `taxonomy.py`  | compact O*NET/SOC-flavored occupation list (codes, titles, keywords) |
| `samples.py`   | bundled, seeded sample conversations (swap for WildChat) |
| `classify.py`  | classifier with `heuristic` / `ollama` / `claude` backends + graceful fallback |
| `reference.py` | reference distribution + Spearman utilities for validation |
| `pipeline.py`  | the end-to-end run, reusing the warehouse / memory / serving layers |

## Honest scope of the offline demo

The bundled sample + `heuristic` backend is for **zero-cost CI/demo**: the sample
requests are templated, so the heuristic classifies them well and the validation
correlation is high *by construction*. The real test of the method is on messy,
real conversations with a real model — which is exactly what the two upgrades below
enable.

## Upgrade 1 — real conversations (WildChat-1M)

[WildChat-1M](https://huggingface.co/datasets/allenai/WildChat-1M) (ODC-BY) is
~838k real human–LLM conversations. Load a sample and feed it to the pipeline:

```python
from datasets import load_dataset  # pip install datasets
ds = load_dataset("allenai/WildChat-1M", split="train", streaming=True)
convs = []
for i, row in enumerate(ds):
    if i >= 5000:
        break
    first_user = next((m["content"] for m in row["conversation"] if m["role"] == "user"), "")
    convs.append({"id": f"wc{i:05d}", "text": first_user[:2000]})
# then: classify_all(convs, backend="ollama", settings) → land → aggregate
```

Cost discipline (mirrors the real AEI): classify a **stratified sample** (20–50k),
use a cheap/local model for the bulk and a stronger one only on a spot-check slice,
cache results, and report cost-per-N.

## Upgrade 2 — real taxonomy + ground-truth validation

- Replace `taxonomy.py` with the full **O*NET task database**
  (https://www.onetcenter.org/database.html, **CC-BY 4.0** — keep attribution).
- Validate against Anthropic's published **`Anthropic/EconomicIndex`** release on
  Hugging Face (CC-BY) instead of the bundled reference — turning "I replicated the
  AEI" into a verifiable claim.

## Cautions (so it stays credible)

- A public proxy corpus (WildChat = ChatGPT traffic) is **not** Claude usage — frame
  every finding as descriptive, with a limitations note.
- Don't claim you matched Anthropic's internal hyperparameters; cite only public
  release thresholds.
- Report classifier quality honestly (a hand-labeled gold set + per-class F1 / Cohen's
  kappa) rather than raw accuracy.
