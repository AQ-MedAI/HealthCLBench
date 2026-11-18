"""生成纯合成数据并走通 CLI；不是医学数据，也不是论文实验结果。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from hclbench.scoring import case_hash, digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="A new directory (must not exist)")
    args = parser.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=False)

    def save(name, obj):
        (root / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def cli(*argv):
        subprocess.run([sys.executable, "-m", "hclbench", *map(str, argv)], check=True)

    cases = []
    for i in (1, 2):
        query = "What is the displayed color?"
        cases.append({"case_id": f"synthetic-{i}", "cluster_id": "synthetic-cluster",
                      "query": query, "messages": [
                          {"role": "user", "content": f"Display color: {'red' if i == 1 else 'blue'}."},
                          {"role": "assistant", "content": "Understood."},
                          {"role": "user", "content": query}],
                      "rubrics": [{"id": 1, "level": "P0", "type": "positive",
                                   "claim": f"The answer identifies {'red' if i == 1 else 'blue'}."}]})
    dataset = dict(schema_version="1.0", dataset_version="synthetic-demo-v1", cases=cases)
    save("dataset.json", dataset)
    cli("validate", "--dataset", root / "dataset.json")
    for arm in ("full", "query"):
        cli("prepare", "--dataset", root / "dataset.json", "--arm", arm, "--output", root / f"{arm}_requests.json")
        requests = json.loads((root / f"{arm}_requests.json").read_text())["requests"]
        predictions = dict(dataset_version=dataset["dataset_version"], dataset_sha256=digest(dataset),
                           model="synthetic-demo", run_id=f"demo-{arm}", arm=arm,
                           results=[dict(case_id=r["case_id"], case_sha256=r["case_sha256"],
                                         input_sha256=r["input_sha256"], status="ok",
                                         answer=("red" if idx == 0 else "blue") if arm == "full" else "unknown")
                                    for idx, r in enumerate(requests)])
        save(f"{arm}_predictions.json", predictions)
        cli("judge-requests", "--dataset", root / "dataset.json", "--predictions", root / f"{arm}_predictions.json",
            "--judges", "judge-A", "judge-B", "judge-C", "--output", root / f"{arm}_judge_requests.json")
        requests = json.loads((root / f"{arm}_judge_requests.json").read_text())["requests"]
        responses = [{k: r[k] for k in ("case_id", "case_sha256", "answer_sha256", "rubric_id", "judge_id")} for r in requests]
        for response in responses:
            response.update(judgment="Met" if arm == "full" else "Not Met", evidence="Synthetic fixture")
        save(f"{arm}_responses.json", responses)
        cli("collect", "--dataset", root / "dataset.json", "--predictions", root / f"{arm}_predictions.json",
            "--responses", root / f"{arm}_responses.json", "--judges", "judge-A", "judge-B", "judge-C",
            "--output", root / f"{arm}_judgments.json")
        cli("score", "--dataset", root / "dataset.json", "--judgments", root / f"{arm}_judgments.json",
            "--output", root / f"{arm}_scores.json")
    cli("compare", "--dataset", root / "dataset.json", "--full", root / "full_judgments.json",
        "--query", root / "query_judgments.json", "--output", root / "comparison.json")
    print((root / "comparison.json").read_text())


if __name__ == "__main__":
    main()
