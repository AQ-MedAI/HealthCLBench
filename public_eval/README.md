# Evaluation framework: technical guide

Start with the [repository quick start](../README.md) or [中文指南](README_ZH.md).
All commands here run from the **repository root** after
`python -m pip install './public_eval[hf]'`.

## Configuration

Use a private copy of [config.example.json](config.example.json).

| Field | Meaning |
|---|---|
| `models` | One or more tested models; unique IDs and endpoint/model pairs |
| `judges` | Exactly three distinct endpoint/model pairs |
| `id` | Output identifier: letters, digits, underscores, hyphens |
| `model` | Provider model name; preserve the exact version when possible |
| `base_url` / `allowed_host` | HTTPS Chat Completions base URL and matching exact hostname |
| `api_key_env` | Environment variable name, never the credential value |
| `parameters` | Explicit `max_tokens`; optional `temperature`, `top_p`, `seed` |
| `workers` | Concurrent calls, 1–32 |
| `timeout_seconds` | Socket-operation timeout, 1–3,600; not a total request deadline |
| `retries` / `retry_delay_seconds` | Extra attempts (0–3) and fixed delay (0–60 seconds) |

The example defaults to 4 workers, 180 seconds, 4,096 inference / 2,048 judge
tokens, and one retry after 10 seconds. These are not universal recommendations.
The successful 2026-10-29 smoke run used 3 workers, 240 seconds and 8,192 tokens.
Reasoning models may consume the budget before emitting final JSON. Calibrate
budgets and supported parameters against your provider. `reasoning_effort`,
Responses API, streaming and direct local model loading are not implemented.

Config is trusted operator input. Never take endpoints or credentials from a
dataset. Requests disable redirects and ambient proxies. HTTP 401/403 abort
inference scoring; permanent HTTP errors are not retried. No credential or raw
HTTP error body is included in diagnostics.

## Dataset and experiment boundaries

Use the labeled Hugging Face CSV by default. Download it once before the commands below:

```sh
mkdir -p downloads
hclbench-download --config public_eval/hf.example.json --output downloads/HealthCLBench.csv
hclbench validate --dataset downloads/HealthCLBench.csv
```

The pinned hash protects CSV release completeness. [Unified JSONL](DATA_FORMAT.md)
remains an offline alternative: replace the CSV path with
`data/release-20261029/HealthCLBench.jsonl`. Use a new run directory when
switching formats. Commands still require `--dataset`; no implicit network calls.
Legacy internal dataset JSON remains supported.

Full sends complete released messages; Query sends only the explicit final
query. Neither includes grading Rubrics. Judges receive full evidence for both
arms and assess only the final answer, not a thinking trace. Their prompt is
frozen in [JUDGE_PROTOCOL.md](JUDGE_PROTOCOL.md). The framework does not prove
that exported messages or the judge prompt reproduce a separate platform/paper.

Select whole clusters with `--clusters`. A subset result must be labelled as a
subset. Keep the same Cases, judges and parameters for paired comparisons.
Record code revision, dataset file SHA-256, semantic dataset hash, Python version,
model versions, generation parameters and judge-protocol hash.

## Advanced dataset configuration

For offline evaluation, replace `downloads/HealthCLBench.csv` in the run commands
with the bundled `data/release-20261029/HealthCLBench.jsonl`. No download is needed.
Use a new run directory when switching formats; `--dataset` is always explicit.

To download another JSONL release, create a separate configuration with its
`repo_id`, immutable `revision`, `filename`, and file-byte `sha256`:

```sh
python -m pip install './public_eval[hf]'
mkdir -p downloads
hclbench-download --config /path/to/hf-source.json --output downloads/HealthCLBench.jsonl
hclbench validate --dataset downloads/HealthCLBench.jsonl
```

For private or gated data, explicitly add `--token-env HF_TOKEN` and supply the
token through that environment variable. The downloader does not send cached
credentials by default. It verifies the checksum and dataset structure and
refuses to overwrite existing files.

## Independent stages / external runners

Use these when your provider is not supported by the integrated runner.
Each output file must not already exist, and its parent directory must exist.

```sh
mkdir -p results/manual-001
hclbench prepare --dataset downloads/HealthCLBench.csv --arm full --output results/manual-001/requests.json
# Generate predictions.json with your own runner, preserving IDs and input hashes.
hclbench judge-requests --dataset downloads/HealthCLBench.csv --predictions results/manual-001/predictions.json --judges judge-a judge-b judge-c --output results/manual-001/judge-requests.json
# Execute the judge requests and save responses.json.
hclbench collect --dataset downloads/HealthCLBench.csv --predictions results/manual-001/predictions.json --responses results/manual-001/responses.json --judges judge-a judge-b judge-c --output results/manual-001/judgments.json
hclbench score --dataset downloads/HealthCLBench.csv --judgments results/manual-001/judgments.json --output results/manual-001/scores.json
```

Repeat preparation with `--arm query` in a separate directory. Compare raw votes:

```sh
hclbench compare --dataset downloads/HealthCLBench.csv --full results/manual-001/judgments.json --query results/manual-query-001/judgments.json --output results/paired-001.json
```

Predictions contain `dataset_version`, `dataset_sha256`, `model`, `run_id`,
`arm`, and `results`. Each result echoes `case_id`, `case_sha256`,
`input_sha256`; success has `status: "ok"` and `answer`, finalized failure has
`status: "failed"` and `failure_reason`. Missing results are rejected.

Judge responses echo `case_id`, `case_sha256`, `answer_sha256`, `rubric_id`,
`judge_id`, with `judgment` and optional `evidence`. `collect` checks identity
and coverage. Canonical judgments add `judge_ids`, answer hashes and per-Rubric
three-vote `judged_items`. Generate the synthetic demo for complete examples.

## Outputs and recovery

A live run saves a manifest, selected dataset snapshot, hashed call checkpoints,
predictions, judgments, Case/cluster scores and a complete `summary.json`.
Technical judge failures produce `judge_failures.jsonl` and partial outputs,
not an artificial Unknown or complete summary.

| Symptom | Action |
|---|---|
| Package index cannot find optional huggingface_hub | Local-file evaluation needs only the base package; use an approved index containing the HF extra when downloading |
| Missing credential / HTTP 401 or 403 | Correct credentials/access; use a new run for finalized inference failures |
| `transport_error` | Check provider/network; resume with unchanged settings or start a new run with revised timeout |
| `incomplete_completion` | Check completion budget/provider stop reason; changed settings require a new run |
| Invalid judge JSON | Inspect private response, model support and budget; do not relabel as Unknown |
| Incomplete cluster | Load all members and select with `--clusters` |
| Output exists / resume identity mismatch | New output directory, or exact unchanged experiment with `--resume` |
| Stale `.lock` after process termination | Confirm no runner is active before removing that specific lock |
| Zero cluster score with successful calls | Inspect member scores; one zero member makes the geometric mean zero |

On resume, successful checkpoints are reused. Finalized failed inference remains
zero; re-evaluating it requires a new run. Failed grading is retried. A run report
must be generated after execution stops, into a new directory. Partial reports
exit 2 and leave unavailable scores blank; only complete pairs get deltas.

Reports contain metrics and model labels, not patient text. Run directories contain
answers and input snapshots: keep them private. Checkpoint attempt counts are
not a complete billing ledger after interruptions/repeated failed attempts.

## Private import and testing

`python -m hclbench.import_snapshot` is an optional offline adapter for a
`num/ext_info` snapshot. It accepts JSON data only, requires explicit current-query
markers, preserves messages/Rubrics and stores runtime fields in a separate
private provenance file. It does not connect to a platform, inject missing context,
certify privacy or perform medical editing.

```sh
python -m unittest discover -s public_eval/tests -q
python public_eval/examples/demo.py --output /tmp/hclbench-demo-002
```

The legacy `examples/verify_manifest.py` CLI expects internal dataset JSON, not
release JSONL. Its verification function is also used for full-population
arithmetic tests after JSONL loading. Synthetic votes test arithmetic, not quality.

Current validation and remaining publication gates are in the
[release checklist](RELEASE_CHECKLIST.md). The test suite does not require API keys.
