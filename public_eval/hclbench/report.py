"""Recompute scores and export a text-free run summary, including partial runs."""
import argparse
import csv
import html
import json
from pathlib import Path

from .__main__ import collect, judge_requests
from .runner import RUNNER_VERSION, check_config, save
from .scoring import compare, digest, evaluate, require, validate_dataset


def read(path):
    require(not path.is_symlink(), "Refuse symlink report input")
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(root):
    root = Path(root)
    require(not (root / ".lock").exists(), "Wait until the run finishes")
    meta, data = read(root / "manifest.json"), read(root / "dataset.json")
    validate_dataset(data)
    require(digest(data) == meta["dataset_sha256"], "Dataset changed")
    require(meta["runner_version"] == RUNNER_VERSION, "Unsupported runner version")
    config = meta["config"]
    check_config(config, require_credentials=False)
    judges = {j["id"]: j for j in config["judges"]}
    rows, deltas = [], []
    for model in config["models"]:
        pair = {}
        for arm in ("full", "query"):
            prefix = model["id"] + "-" + arm
            row = dict(model=model["model"], arm=arm, status="not_started", saved_votes=0,
                       expected_votes=None, successful_answers=0, failed_answers=0,
                       case_mean_100=None, cluster_mean_100=None)
            pred_path = root / (prefix + "-predictions.json")
            if pred_path.exists():
                predictions = read(pred_path)
                require(predictions["model"] == model["model"] and predictions["arm"] == arm,
                        "Prediction identity mismatch")
                reqs = judge_requests(data, predictions, list(judges))["requests"]
                responses = []
                for req in reqs:
                    key = digest({"model": judges[req["judge_id"]], "request": req, "judge": True,
                                  "scope": prefix, "runner": RUNNER_VERSION})
                    checkpoint = root / "calls" / (key + ".json")
                    if checkpoint.exists():
                        vote = read(checkpoint)
                        require(vote["request_sha256"] == key and vote["status"] == "ok", "Bad checkpoint")
                        row["saved_votes"] += 1
                        response = {k: req[k] for k in ("case_id", "case_sha256", "answer_sha256", "rubric_id", "judge_id")}
                        response.update(judgment=vote["judgment"], evidence=vote["evidence"])
                        responses.append(response)
                row.update(status="incomplete", expected_votes=len(reqs),
                           successful_answers=sum(r["status"] == "ok" for r in predictions["results"]),
                           failed_answers=sum(r["status"] == "failed" for r in predictions["results"]))
                jp = root / (prefix + "-judgments.json")
                if jp.exists():
                    judgment = read(jp)
                    require(judgment == collect(data, predictions, responses, list(judges)),
                            "Judgments differ from predictions and checkpoints")
                    require(judgment["model"] == model["model"] and judgment["arm"] == arm
                            and judgment["judge_ids"] == list(judges), "Judgment identity mismatch")
                    scored = evaluate(data, judgment)
                    require(row["saved_votes"] == row["expected_votes"], "Missing checkpoints")
                    stored = root / (prefix + "-scores.json")
                    if stored.exists():
                        require(read(stored) == scored, "Stored score differs from recomputed score")
                    pair[arm] = scored
                    row.update(status="complete", **scored["summary"])
            rows.append(row)
        if len(pair) == 2:
            deltas.append(compare(pair["full"], pair["query"]))
    return {"dataset_sha256": digest(data), "all_complete": all(r["status"] == "complete" for r in rows),
            "paper_prompt_parity": meta.get("paper_prompt_parity", False),
            "self_judging_possible": meta.get("self_judging_possible", True), "rows": rows, "deltas": deltas}


def export(report, target):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    save(target / "summary.json", report)
    fields = ["model", "arm", "status", "successful_answers", "failed_answers", "saved_votes",
              "expected_votes", "case_mean_100", "cluster_mean_100"]
    def cell(value):
        value = "" if value is None else str(value)
        # COSEC: model labels are untrusted spreadsheet cells; prevent formula execution.
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
    with (target / "summary.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows([[cell(row.get(f)) for f in fields] for row in report["rows"]])
    def md(value):
        return html.escape(str(value)).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")
    lines = ["# Evaluation summary", "", "Complete: " + str(report["all_complete"]), "",
             "Missing scores are not zero. See summary.json for paired Full/Query deltas.", "",
             "| Model | Arm | Status | Votes | Case /100 | Cluster /100 |", "|---|---|---|---:|---:|---:|"]
    for row in report["rows"]:
        lines.append("| " + " | ".join(md(v) for v in [row["model"], row["arm"], row["status"],
                     str(row["saved_votes"]) + "/" + str(row["expected_votes"]),
                     row["case_mean_100"], row["cluster_mean_100"]]) + " |")
    (target / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-dir", required=True, help="New directory")
    args = parser.parse_args()
    try:
        report = summarize(args.run_dir)
        export(report, args.output_dir)
    except (ValueError, KeyError, TypeError, OSError):
        raise SystemExit("Report refused: check run identity, completion, files and output directory")
    print(json.dumps({"all_complete": report["all_complete"], "arms": len(report["rows"])}))
    if not report["all_complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
