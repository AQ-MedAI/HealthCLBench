"""Offline, lossless message/Rubric import. No database access or data rewriting.

Runtime platform fields are retained in a separate private provenance file, not
silently rendered as model instructions. This import does not certify EVE parity.
"""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import re

from .scoring import digest, require, validate_dataset


def convert(snapshot, dataset_version):
    require(snapshot["columns"] == ["num", "ext_info"], "Expected num/ext_info snapshot")
    cases, provenance, findings = [], [], []
    numbers = set()
    for num, raw in snapshot["rows"]:
        require(str(num) not in numbers, "Duplicate source num")
        numbers.add(str(num))
        ext = json.loads(raw)
        params = ext["extParams"]
        groups = params["eveAdditionalInfo"]
        require(len(groups) == 1 and set(groups[0]) == {"messages"}, "Ambiguous message groups")
        messages = copy.deepcopy(groups[0]["messages"])
        require(messages and messages[-1]["role"] == "user", "Missing final user")
        final = messages[-1]["content"]
        require(isinstance(final, str), "Cannot extract query from nontext final message")
        require(final.count("<current_user_message>") == 1 and final.count("</current_user_message>") == 1,
                "Expected one explicit current query marker")
        match = re.search(r"<current_user_message>(.*?)</current_user_message>", final, re.S)
        require(match is not None and bool(match[1].strip()), "Empty current query")
        start = match.start(1) + len(match[1]) - len(match[1].lstrip())
        query = match[1].strip()
        case = {"case_id": ext["child_case_id"], "cluster_id": ext["parent_case_id"],
                "query": query, "query_span": {"start": start, "end": start + len(query)},
                "messages": messages, "rubrics": copy.deepcopy(ext["rubrics"])}
        cases.append(case)
        runtime = {k: v for k, v in params.items() if k != "eveAdditionalInfo"}
        provenance.append({"case_id": case["case_id"], "num": num, "source_ext_sha256": digest(ext),
                           "messages_sha256": digest(messages), "rubrics_sha256": digest(case["rubrics"]),
                           "runtime_context_not_rendered": runtime})
        def flag(kind, **detail):
            findings.append(dict(case_id=case["case_id"], kind=kind, **detail))
        adjacent = [i for i in range(1, len(messages)) if messages[i]["role"] == messages[i-1]["role"]]
        if adjacent:
            flag("adjacent_same_role", message_indices=adjacent)
        blocks = [i for i, m in enumerate(messages) if isinstance(m["content"], list)]
        if blocks:
            flag("text_block_content_preserved", message_indices=blocks)
        claims = Counter(r["claim"] for r in case["rubrics"])
        if any(n > 1 for n in claims.values()):
            flag("duplicate_claim", claims=[s for s, n in claims.items() if n > 1])
        for r in case["rubrics"]:
            if r["type"] == "negative" and re.search(r"不得|不能|不应|禁止|避免|不要", r["claim"]):
                flag("negative_prohibition_wording_review", rubric_id=r["id"])
    dataset = {"schema_version": "1.0", "dataset_version": dataset_version, "cases": cases}
    validate_dataset(dataset)
    report = {"cases": len(cases), "clusters": len({c["cluster_id"] for c in cases}),
              "rubrics": sum(len(c["rubrics"]) for c in cases),
              "rubric_levels": dict(Counter(r["level"] for c in cases for r in c["rubrics"])),
              "rubric_types": dict(Counter(r["type"] for c in cases for r in c["rubrics"])),
              "cluster_sizes": dict(Counter(Counter(c["cluster_id"] for c in cases).values())),
              "p0_per_case": dict(Counter(sum(r["level"] == "P0" for r in c["rubrics"]) for c in cases)),
              "finding_counts_by_case": dict(Counter(f["kind"] for f in findings if "rubric_id" not in f)),
              "negative_prohibition_candidates": sum(f["kind"] == "negative_prohibition_wording_review" for f in findings),
              "findings": findings, "dataset_sha256": digest(dataset),
              "messages_and_rubrics_unchanged": True,
              "runtime_context_preserved_in_provenance_not_injected": True,
              "eve_runtime_parity_verified": False,
              "medical_semantics_reviewed": False, "release_ready": False}
    return dataset, provenance, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--output-dir", required=True, help="Private directory outside Git")
    args = parser.parse_args()
    dataset, provenance, report = convert(json.loads(Path(args.snapshot).read_text()), args.dataset_version)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=False)
    for name, value in (("dataset.json", dataset), ("provenance.private.json", provenance), ("audit.json", report)):
        with (out / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps({k: v for k, v in report.items() if k != "findings"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
