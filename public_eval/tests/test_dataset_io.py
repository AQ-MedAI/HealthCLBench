import json
import tempfile
from pathlib import Path
import unittest

from hclbench.dataset_io import from_jsonl, load_dataset, to_jsonl
from hclbench.__main__ import prepare
from hclbench.scoring import digest, evaluate
from test_scoring import fixture


class DatasetIOTests(unittest.TestCase):
    def test_case_label_round_trip_without_changing_model_inputs(self):
        dataset, _ = fixture()
        def inputs(arm):
            return [r["messages"] for r in prepare(dataset, arm)["requests"]]
        before = {arm: inputs(arm) for arm in ("full", "query")}
        dataset["cases"][0]["label"] = {"medical_scenario": "synthetic", "departments": []}
        self.assertEqual(from_jsonl(to_jsonl(dataset)), dataset)
        self.assertEqual(before, {arm: inputs(arm) for arm in before})

    def test_lossless_content_and_scores(self):
        dataset, votes = fixture()
        self.assertEqual(evaluate(from_jsonl(to_jsonl(dataset)), votes), evaluate(dataset, votes))
        for c in dataset["cases"]:
            c["messages"].insert(0, {"role": "user", "content": [
                {"type": "text", "text": "第一段\n"}, {"type": "text", "text": "第二段"}]})
        restored = from_jsonl(to_jsonl(dataset))
        self.assertEqual(restored, dataset)
        self.assertEqual(digest(restored), digest(dataset))
        for arm in ("full", "query"):
            self.assertEqual(prepare(restored, arm), prepare(dataset, arm))

    def test_missing_member_and_duplicate_rejected(self):
        dataset, _ = fixture()
        lines = to_jsonl(dataset).splitlines()
        for broken in (lines[:-1], lines + [lines[0]]):
            with self.assertRaises(ValueError):
                from_jsonl(b"\n".join(broken))
        # A complete cluster subset is valid, a partial one is not.
        self.assertEqual(len(from_jsonl(lines[0])["cases"]), 1)

    def test_versions_and_membership_must_agree(self):
        dataset, _ = fixture()
        for key, value in (("dataset_version", "other"), ("cluster_case_ids", ["b"])):
            rows = [json.loads(x) for x in to_jsonl(dataset).splitlines()]
            rows[1]["metadata"][key] = value
            with self.assertRaises(ValueError):
                from_jsonl("\n".join(json.dumps(r) for r in rows).encode())

    def test_load_both_formats(self):
        dataset, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            for name, raw in (("data.jsonl", to_jsonl(dataset)), ("data.json", json.dumps(dataset).encode())):
                p = Path(tmp) / name
                p.write_bytes(raw)
                self.assertEqual(load_dataset(p), dataset)
