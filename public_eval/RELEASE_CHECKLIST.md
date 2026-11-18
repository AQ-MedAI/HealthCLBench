# Release checklist and reference comparison

Reviewed against the official repositories on **2026-10-29**.
This compares project usability and distribution, not benchmark novelty.
Local documentation and test status updated on **2026-11-18**; historical
verification dates below are retained where checks have not been repeated.

## Reference comparison

| Area | LongBench | CL-bench family | HealthCLBench |
|---|---|---|---|
| Start page | Task overview, dataset, schema, run commands, citation | Overview, Hub files, inference/evaluation, citation | Root quick start, bilingual guide, data and protocol links |
| Data | Hub loading/direct file | Hub JSONL | Bundled CSV/JSONL; pinned Hub CSV configuration; end-to-end download verification pending |
| Execution | v2 documents vLLM serving and prediction | Separate inference and judge scripts | Integrated Chat Completions runner plus separate stages/external runner |
| Scoring | Version/task-specific; v2 multiple choice | All Rubrics must pass for task success | Three-judge majority, weighted P0 gate, Case and cluster scores |
| Reproduction | Public experiment instructions | Judge defaults and reasoning settings | Frozen prompt/hash, explicit config; paper-parity validation pending |
| Public results | Project leaderboard | Leaderboard submission instructions | Manuscript Table 6 transcribed; independent reproduction and public artifacts pending |

Sources: [LongBench repository](https://github.com/THUDM/LongBench)
(the root describes v2; original v1 is under LongBench/),
[CL-bench repository](https://github.com/Tencent-Hunyuan/CL-bench).
These references provide a usability comparison, not a statement of implementation provenance.

## Implemented and checked

- [x] Python 3.11+, installable CLI, synthetic demo; 46 unit tests pass on 2026-11-18.
- [x] Base package built and installed into a fresh virtual environment on
  2026-10-29; root-directory quick-start tests, demo and local data validation checked.
- [x] Unified JSONL, complete-cluster checks, lossless input restoration.
- [x] Final local release: 1,103 Cases / 328 clusters / 4,931 Rubrics.
- [x] Full-population input checks and four independent synthetic arithmetic scenarios.
- [x] Configurable endpoints, three judges, bounded retries, hashes and resumable checkpoints.
- [x] Separate prediction, judging, collection, scoring and comparison contracts.
- [x] JSON/CSV/Markdown report independently recomputes scores; incomplete runs labelled.
- [x] Judge protocol v1 frozen with input-boundary/hash regression tests.
- [x] Real final-data smoke: 3 Cases in one complete cluster, Full and Query,
  6 successful answers, 54 successful votes, one retried call; report complete.
- [x] Apache-2.0 code license and clean release-branch layout.
- [x] README includes manuscript dataset statistics, method overview and main
  results, clearly distinguished from engineering smoke tests. Tables 4 and 6
  were visually checked in the owner-supplied manuscript on 2026-10-29.

The latest successful smoke used Qwen3.6-35B-A3B inference and
Kimi-K2.6/Qwen3.6-35B-A3B/GLM-5.2 judging, 3 workers, 240-second socket timeout,
8,192-token limits, one retry after 10 seconds. The larger initial attempt had
transport/incomplete-output failures. This is functional evidence, not full-scale
reliability, independent judging, clinical validation or paper reproduction.

## Remaining release gates

- [ ] Confirm organizational release approval and dataset redistribution/consent rights.
- [ ] Complete an end-to-end Hub download and Viewer check. The example configuration
  pins the CSV revision and checksum; the data card remains minimal.
- [ ] Verify installation of the optional HF dependency in the release environment.
  The current environment could not resolve huggingface_hub from its default index
  or an explicit public PyPI attempt; base-package installation succeeded.
- [x] Include the public arXiv URL and author-supplied BibTeX citation.
- [ ] Verify paper-aligned protocol and model settings.
- [ ] Publish reproducible baseline results with code/data/model revisions and full
  parameter records; distinguish complete and subset experiments.
- [ ] Confirm public maintainers and security-contact route.
- [ ] Run the configured CI on the eventual public host; local tests are not proof
  that the hosted matrix passed.
- [ ] Publish only the clean release branch and approved data; do not mirror private
  branches, tags, credentials, model responses or internal history.

## Optional follow-ups, not blockers for the supported workflow

- A ready-to-run local model deployment recipe, if local serving is a target use case.
  Current integrated execution requires an operator-managed HTTPS endpoint.
- Provider-specific reasoning controls or alternate APIs, only with tests and
  documented semantics. Do not silently insert unsupported parameters.
- Category-level results, if reviewed category metadata becomes available.
- A public leaderboard/submission process once official baseline and citation
  artifacts exist. Do not publish smoke-test scores as leaderboard entries.

## Result submission / reproducibility record

Use the repository issue/PR mechanism with a sanitized report, code revision,
dataset file SHA-256, model/provider versions, three judge identities, generation
parameters and judge-protocol hash. State the selected clusters, inference
failures, unresolved Rubrics and whether any tested model is also a judge.
Never attach secrets or private input/response checkpoints.

Saved votes permit deterministic score recomputation. Fresh calls may differ
because hosted aliases and model outputs can change even at temperature zero.
A cluster with a zero member scores zero; larger clusters have more opportunities
for a zero member, despite equal weights across clusters.
