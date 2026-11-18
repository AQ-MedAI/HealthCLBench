# Dataset formats: CSV and JSONL

## Hugging Face CSV input

The loader accepts the labeled Hub CSV with columns
`num,messages,rubrics,label,cluster_id`. It parses nested fields as JSON (never
executable code), preserves labels and messages, and maps numeric IDs to
`case-NNNN` / `cluster-NNNN`. Query-only input is extracted exclusively from one
explicit `<current_user_message>` pair in the final user message; missing or
ambiguous markers are rejected, not guessed.

CSV dataset identity is `csv-sha256-<file hash>`, so existing JSONL run directories
must not be reused for CSV runs. CSV has no independent expected-membership field:
use the pinned full-release SHA-256 to protect completeness, and use `--clusters`
after loading the full file instead of manually trimming rows.

## JSONL representation

One case per line, with `messages`, `rubrics`, and `metadata`. The current
Hugging Face release publishes CSV; the repository also includes JSONL as an
offline alternative. Both formats are supported for inference and scoring.

```json
{"messages":[{"role":"user","content":[{"type":"text","text":"Synthetic question?"}]}],"rubrics":[{"id":1,"claim":"Synthetic criterion","level":"P1","type":"positive"}],"metadata":{"schema_version":"1.0","dataset_version":"synthetic-v1","case_id":"a","cluster_id":"g1","query":"Synthetic question?","query_span":null,"original_content_types":["string"],"cluster_case_ids":["a"]}}
```

## Lossless messages

Every message in the bundled JSONL uses a list of text blocks for `content`, avoiding mixed
string/list columns in Arrow. `metadata.original_content_types` has one entry per
message: `string` restores a single text block to its original string, `blocks`
preserves the original array. This is storage normalization only: the framework
restores exact inference messages before calling a model or hashing a case.
Do not send storage metadata to a model. The current release is text-only;
unsupported media blocks are rejected, never silently removed.

The current question is retained verbatim in `metadata.query`. Optional
`query_span` offsets refer to the restored final user string; null means the
entire final user content is the query. Both experiment arms preserve this query.
Rubric id/claim/level/type are unchanged and must never be included in the tested
model request.

## Complete clusters and release identity

The annotated release stores the source CSV's structured case classifications
in `metadata.label`. These are analysis metadata, not inference or judge inputs.
Older JSONL files without this field remain readable.

`cluster_case_ids` is the complete expected member list, repeated in each member.
Loading a file with missing/duplicate members or inconsistent member lists fails.
Load the full file and use `hclbench-run --clusters ...` to select complete clusters.
A subset of complete clusters is also valid, but is a subset experiment, not a
full-release result. A manifest separately records all clusters, counts and file
SHA-256; the configured download SHA-256 prevents undetected file edits. Embedded
membership is an integrity check, not cryptographic authenticity by itself.

## Commands

Run from the repository root after installing `./public_eval`.

```sh
hclbench validate --dataset data/release-20261029/HealthCLBench.jsonl
hclbench-run --dataset data/release-20261029/HealthCLBench.jsonl --config public_eval/config.example.json --output-dir results/run-001 --dry-run
hclbench prepare --dataset data/release-20261029/HealthCLBench.jsonl --arm full --output full-requests.json
hclbench score --dataset data/release-20261029/HealthCLBench.jsonl --judgments judgments.json --output scores.json
```

Other dataset-accepting commands (`judge-requests`, `collect`, `compare`) use the
same JSONL. JSON inference outputs and votes remain unchanged. Legacy dataset
JSON remains readable for existing runs; internal run snapshots remain JSON and
are not a second public dataset. Scoring, judge prompts and existing cache hashes
are unchanged after a lossless conversion.
