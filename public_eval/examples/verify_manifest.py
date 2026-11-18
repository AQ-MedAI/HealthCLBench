"""Synthetic votes on any manifest: arithmetic regression, NOT medical/model evaluation."""
import argparse
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_EVEN
import json
import math
from pathlib import Path

from hclbench.scoring import case_hash, digest, evaluate, validate_dataset


def verify(dataset):
    validate_dataset(dataset)
    summaries = {}
    judges = ["synthetic-A", "synthetic-B", "synthetic-C"]
    for scenario in ("all_pass", "all_fail", "all_unknown", "mixed"):
        rows, reference = [], {}
        for ci, case in enumerate(dataset["cases"]):
            items, earned, deducted, denominator, veto = [], 0, 0, 0, False
            for ri, rubric in enumerate(case["rubrics"]):
                state = {"all_pass": 0, "all_fail": 1, "all_unknown": 2}.get(scenario, (ci + ri) % 3)
                # Separate reference arithmetic, driven by semantic pass/fail/unknown,
                # not the production scorer's mapped decisions or returned weights.
                weight = {"P0": 3, "P1": 2, "P2": 1}[rubric["level"]]
                denominator += weight
                if state == 0:
                    earned += weight
                elif state == 1:
                    if rubric["level"] == "P0":
                        veto = True
                    else:
                        deducted += weight
                good = "Met" if rubric["type"] == "positive" else "Not Met"
                bad = "Not Met" if good == "Met" else "Met"
                vote = (good, bad, "Unknown")[state]
                items.append(dict(rubric, votes=[{"judge_id": j, "judgment": vote} for j in judges]))
            expected = Decimal(0) if veto else Decimal(max(0, earned - deducted)) / denominator
            reference[case["case_id"]] = expected.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
            rows.append({"case_id": case["case_id"], "case_sha256": case_hash(case),
                         "input_sha256": digest(case["messages"]), "status": "ok",
                         "answer": "SYNTHETIC verification only", "answer_sha256": digest("SYNTHETIC verification only"),
                         "judged_items": items})
        run = {"dataset_version": dataset["dataset_version"], "dataset_sha256": digest(dataset),
               "model": "SYNTHETIC-NOT-A-MODEL", "run_id": scenario, "arm": "full",
               "judge_ids": judges, "results": rows}
        actual = evaluate(dataset, run)
        for row in actual["cases"]:
            assert Decimal(row["score_raw4"]) == reference[row["case_id"]]
        grouped = defaultdict(list)
        for case in dataset["cases"]:
            grouped[case["cluster_id"]].append(float(reference[case["case_id"]]))
        refs = {k: 0 if 0 in vals else math.exp(sum(map(math.log, vals))/len(vals)) for k, vals in grouped.items()}
        for cluster in actual["clusters"]:
            assert abs(float(cluster["score_0_1"]) - refs[cluster["cluster_id"]]) < 1e-12
        expected_mean = sum(refs.values()) * 100 / len(refs)
        assert abs(float(actual["summary"]["cluster_mean_100"]) - expected_mean) < 1e-10
        expected_case_mean = sum(map(float, reference.values())) * 100 / len(reference)
        assert abs(float(actual["summary"]["case_mean_100"]) - expected_case_mean) < 1e-10
        summaries[scenario] = actual["summary"]
    return {"synthetic_votes_only": True, "model_calls": 0, "dataset_sha256": digest(dataset),
            "case_comparisons": len(dataset["cases"]) * 4, "scenarios": summaries, "passed": True}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    result = verify(json.loads(Path(args.dataset).read_text()))
    with Path(args.output).open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result, indent=2))
