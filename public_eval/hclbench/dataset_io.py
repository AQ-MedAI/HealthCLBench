"""Single JSONL release format, losslessly adapted to the scoring representation."""
from collections import defaultdict
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import re

from .scoring import require, validate_dataset


def to_jsonl(dataset):
    validate_dataset(dataset)
    groups = defaultdict(list)
    for case in dataset["cases"]:
        groups[case["cluster_id"]].append(case["case_id"])
    rows = []
    for case in dataset["cases"]:
        messages, formats = [], []
        for message in case["messages"]:
            require(set(message) == {"role", "content"}, "Unexpected message fields")
            content = message["content"]
            formats.append("string" if isinstance(content, str) else "blocks")
            messages.append({"role": message["role"], "content":
                             [{"type": "text", "text": content}] if isinstance(content, str)
                             else copy.deepcopy(content)})
        require(set(case) <= {"case_id", "cluster_id", "query", "query_span", "messages", "rubrics", "label"},
                "Unexpected case fields; cannot silently discard data")
        meta = {k: case[k] for k in ("case_id", "cluster_id", "query")}
        if "label" in case:
            require(isinstance(case["label"], dict), "Expected case label object")
            meta["label"] = case["label"]
        meta.update(schema_version="1.0", dataset_version=dataset["dataset_version"],
                    query_span=case.get("query_span"), original_content_types=formats,
                    cluster_case_ids=groups[case["cluster_id"]])
        rows.append({"messages": messages, "rubrics": case["rubrics"], "metadata": meta})
    return ("\n".join(json.dumps(r, ensure_ascii=False, allow_nan=False) for r in rows) + "\n").encode("utf-8")


def from_jsonl(raw):
    # COSEC: JSON data only; never evaluate remote dataset code or serialized objects.
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    require(bool(rows), "Empty JSONL dataset")
    cases, versions, memberships = [], set(), {}
    actual = defaultdict(set)
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"messages", "rubrics", "metadata"},
                "Expected messages/rubrics/metadata")
        m = row["metadata"]
        require(isinstance(m, dict) and set(m) - {"label"} == {"case_id", "cluster_id", "query", "query_span",
                "schema_version", "dataset_version", "original_content_types", "cluster_case_ids"},
                "Unexpected JSONL metadata schema")
        require(m["schema_version"] == "1.0", "Unsupported JSONL schema")
        versions.add(m["dataset_version"])
        members = m["cluster_case_ids"]
        require(isinstance(members, list) and members and all(isinstance(s, str) and s for s in members)
                and len(set(members)) == len(members), "Invalid cluster member list")
        gid = m["cluster_id"]
        require(gid not in memberships or memberships[gid] == set(members), "Conflicting cluster membership")
        memberships[gid] = set(members)
        actual[gid].add(m["case_id"])
        messages = row["messages"]
        formats = m["original_content_types"]
        require(isinstance(messages, list) and isinstance(formats, list) and len(messages) == len(formats),
                "Invalid original content types")
        restored = []
        for message, kind in zip(messages, formats):
            require(isinstance(message, dict) and set(message) == {"role", "content"}, "Invalid message")
            blocks = message["content"]
            require(isinstance(blocks, list) and blocks and all(isinstance(b, dict) and
                    set(b) == {"type", "text"} and b["type"] == "text" and isinstance(b["text"], str)
                    for b in blocks), "Expected uniform text blocks")
            require(kind in ("string", "blocks"), "Invalid content type")
            require(kind != "string" or len(blocks) == 1, "Cannot reconstruct a string from multiple blocks")
            restored.append({"role": message["role"], "content": blocks[0]["text"] if kind == "string" else blocks})
        case = {k: m[k] for k in ("case_id", "cluster_id", "query")}
        if "label" in m:
            require(isinstance(m["label"], dict), "Expected case label object")
            case["label"] = m["label"]
        case.update(messages=restored, rubrics=row["rubrics"])
        if m["query_span"] is not None:
            case["query_span"] = m["query_span"]
        cases.append(case)
    require(len(versions) == 1, "Mixed dataset versions")
    require(dict(actual) == memberships, "Incomplete cluster: load all members, then use --clusters")
    dataset = dict(schema_version="1.0", dataset_version=versions.pop(), cases=cases)
    validate_dataset(dataset)
    return dataset


def from_csv(raw):
    """Read the labeled release CSV; require an explicit current-query span."""
    previous_limit = csv.field_size_limit()
    try:
        csv.field_size_limit(max(previous_limit, len(raw)))
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        require(reader.fieldnames == ["num", "messages", "rubrics", "label", "cluster_id"],
                "Expected labeled release CSV columns")
        cases = []
        for row in reader:
            require(None not in row and all(v is not None for v in row.values()), "Invalid CSV row")
            require(all(re.fullmatch(r"[1-9][0-9]*", row[k]) for k in ("num", "cluster_id")),
                    "Expected positive numeric case and cluster IDs")
            messages = json.loads(row["messages"])
            require(isinstance(messages, list) and bool(messages)
                    and isinstance(messages[-1], dict), "Missing messages")
            final = messages[-1].get("content")
            require(isinstance(final, str) and final.count("<current_user_message>") == 1
                    and final.count("</current_user_message>") == 1, "Missing or ambiguous current query")
            match = re.search(r"<current_user_message>(.*?)</current_user_message>", final, re.S)
            require(match is not None and bool(match[1].strip()), "Empty current query")
            query = match[1].strip()
            start = match.start(1) + len(match[1]) - len(match[1].lstrip())
            label = json.loads(row["label"])
            require(isinstance(label, dict), "Expected case label object")
            cases.append(dict(case_id=f"case-{int(row['num']):04d}",
                              cluster_id=f"cluster-{int(row['cluster_id']):04d}",
                              messages=messages, rubrics=json.loads(row["rubrics"]), label=label,
                              query=query, query_span=dict(start=start, end=start + len(query))))
    finally:
        csv.field_size_limit(previous_limit)
    dataset = dict(schema_version="1.0",
                   dataset_version="csv-sha256-" + hashlib.sha256(raw).hexdigest(), cases=cases)
    validate_dataset(dataset)
    return dataset


def decode_dataset(raw, *, jsonl=False, csv_format=False):
    require(not (jsonl and csv_format), "Conflicting dataset formats")
    dataset = from_csv(raw) if csv_format else from_jsonl(raw) if jsonl else json.loads(raw)
    validate_dataset(dataset)
    return dataset


def load_dataset(path):
    path = Path(path)
    return decode_dataset(path.read_bytes(), jsonl=path.suffix.lower() == ".jsonl",
                          csv_format=path.suffix.lower() == ".csv")
