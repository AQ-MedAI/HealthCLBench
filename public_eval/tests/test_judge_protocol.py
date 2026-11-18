import json
import unittest

from hclbench.__main__ import JUDGE_PROTOCOL, judge_requests, prepare
from hclbench.scoring import digest
from test_scoring import fixture


class JudgeProtocolTests(unittest.TestCase):
    def test_frozen_prompt(self):
        self.assertEqual(digest(JUDGE_PROTOCOL),
                         "db9cb449cc84d903b22685000507cb2be94a29bfb2592cf1f5f12a3df6127fa3")

    def test_full_query_share_judge_evidence_not_model_input(self):
        dataset, _ = fixture()
        for case in dataset["cases"]:
            case["messages"] = [{"role": "user", "content": "Synthetic history"},
                                {"role": "assistant", "content": "Synthetic historical answer"},
                                *case["messages"]]
        payloads = []
        for arm in ("full", "query"):
            manifest = prepare(dataset, arm)
            predictions = {"arm": arm, "model": "synthetic", "run_id": arm,
                           "dataset_version": dataset["dataset_version"],
                           "dataset_sha256": digest(dataset), "results": []}
            for request in manifest["requests"]:
                if arm == "query":
                    self.assertEqual(request["messages"],
                                     [{"role": "user", "content": "Synthetic question?"}])
                row = {k: request[k] for k in ("case_id", "case_sha256", "input_sha256")}
                predictions["results"].append(dict(row, status="ok", answer="Synthetic final answer"))
            result = judge_requests(dataset, predictions, ["A", "B", "C"])
            self.assertEqual(result["protocol"], "hclbench-public-judge-v1")
            self.assertFalse(result["paper_prompt_parity"])
            payloads.append([r["messages"] for r in result["requests"]])
        self.assertEqual(payloads[0], payloads[1])
        payload = json.loads(payloads[0][0][1]["content"])
        self.assertEqual(set(payload), {"context", "question", "answer", "rubric"})
        self.assertEqual(payload["context"], dataset["cases"][0]["messages"])
        self.assertEqual(payload["answer"], "Synthetic final answer")
