# Final benchmark dataset — 2026-10-29

1,103 Cases, 328 complete clusters, 4,931 Rubrics.

Dataset license: [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
See [LICENSE](LICENSE) for scope and attribution.

Public dataset: [AQ-MedAI/HealthCLBench on Hugging Face](https://huggingface.co/datasets/AQ-MedAI/HealthCLBench)
([CSV download](https://huggingface.co/datasets/AQ-MedAI/HealthCLBench/resolve/main/HealthCLBench.csv)).
The Hub CSV and the bundled JSONL are both directly supported for evaluation.

- `HealthCLBench.csv`: unchanged source contents supplied by the dataset owner.
- `HealthCLBench.jsonl`: runtime input for the framework.
- `manifest.json`: checksums, mapping provenance and offline verification results.

The annotated CSV has `num`, `messages`, `rubrics`, `label` and `cluster_id`.
Every case has a structured label covering medical scenario, context difficulty,
department and disease annotations. Empty optional annotations retain their source
values. JSONL exposes the same object as `metadata.label`. CSV cluster numbers
correspond to the existing public `cluster-NNNN` identifiers. Case IDs and final
query spans are retained from the validated snapshot.
Messages and Rubrics are unchanged. CSV and JSONL are supported representations
of the same release, not separately maintained evaluation variants. Their file
structures and hashes differ; use a new run directory when switching formats.

From the repository root:

```sh
python -m pip install ./public_eval
hclbench validate --dataset data/release-20261029/HealthCLBench.jsonl
hclbench-run --dataset data/release-20261029/HealthCLBench.jsonl --config public_eval/config.example.json --output-dir /tmp/hclbench-run --dry-run
```

To make real model calls, use a private model configuration and environment
credentials, remove `--dry-run`, and choose a new output directory. The example
configuration intentionally contains placeholders. Validation here uses
synthetic votes and a simulated transport, not medical model-quality scores.
