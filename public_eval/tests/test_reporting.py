import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hclbench.planning import plan
from hclbench.report import export, summarize
from hclbench.runner import run
from test_runner import config
from test_scoring import fixture


def transport(model, messages, timeout):
    return {"text": "Synthetic answer" if model["id"] == "tested" else
            '{"judgment":"Met","evidence":"Synthetic"}'}


class ReportingTests(unittest.TestCase):
    def test_plan_without_keys_or_calls(self):
        data, _ = fixture()
        with patch.dict(os.environ, {}, clear=True):
            result = plan(data, config())
        self.assertEqual(result["inference_requests"], 8)
        self.assertEqual(result["judge_requests_if_all_inference_succeeds"], 24)
        self.assertEqual(result["max_attempts_one_pass"], 64)
        self.assertFalse(result["credential_present"]["TEST_BENCH_KEY"])
        self.assertNotIn("example.com", json.dumps(result))

    @patch.dict(os.environ, {"TEST_BENCH_KEY": "synthetic-only"})
    def test_complete_export_and_tampered_score(self):
        data, _ = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "run"
            run(data, config(), root, transport=transport)
            result = summarize(root)
            self.assertTrue(result["all_complete"])
            self.assertEqual(len(result["deltas"]), 1)
            target = Path(temp) / "report"
            export(result, target)
            self.assertTrue((target / "summary.csv").exists())
            self.assertNotIn("Synthetic answer", (target / "summary.json").read_text())
            with self.assertRaises(FileExistsError):
                export(result, target)
            p = root / "tested-full-scores.json"
            score = json.loads(p.read_text())
            score["summary"]["case_mean_100"] = "1"
            p.write_text(json.dumps(score))
            with self.assertRaises(ValueError):
                summarize(root)

    @patch.dict(os.environ, {"TEST_BENCH_KEY": "synthetic-only"})
    def test_partial_has_no_fake_scores(self):
        data, _ = fixture()
        cfg = config()
        cfg["retries"] = 0
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "run"
            with self.assertRaises(ValueError):
                run(data, cfg, root, transport=lambda *args: {"text": "bad judge JSON"})
            result = summarize(root)
            self.assertFalse(result["all_complete"])
            self.assertEqual(result["deltas"], [])
            self.assertTrue(all(r["case_mean_100"] is None for r in result["rows"]))

    def test_export_formula_safety(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "report"
            export({"all_complete": False, "rows": [{"model": "=1+1", "arm": "full", "status": "incomplete",
                   "saved_votes": 0, "expected_votes": 1, "case_mean_100": None, "cluster_mean_100": None}]}, root)
            self.assertIn("'=1+1", (root / "summary.csv").read_text(encoding="utf-8-sig"))
