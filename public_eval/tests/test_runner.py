import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from hclbench.runner import chat, check_config, execute_call, parse_judge, run, select_clusters, NoRedirect
from test_scoring import fixture


def config():
    def model(i):
        return {"id": i, "model": i, "base_url": "https://example.com/v1", "allowed_host": "example.com",
                "api_key_env": "TEST_BENCH_KEY", "parameters": {"temperature": 0, "max_tokens": 500}}
    return {"models": [model("tested")], "judges": [model(i) for i in ("A", "B", "C")],
            "workers": 2, "timeout_seconds": 10, "retries": 1, "retry_delay_seconds": 10}


class RunnerTests(unittest.TestCase):
    @patch.dict(os.environ, {"TEST_BENCH_KEY": "synthetic-test-only"})
    def test_http_payload_and_truncated_completion(self):
        cfg = config()
        messages = [{"role": "user", "content": "Synthetic question"}]
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": "Synthetic answer"}}], "model": "tested"}
        with patch("hclbench.runner.build_opener") as opener:
            reader = opener.return_value.open.return_value.__enter__.return_value.read
            reader.return_value = json.dumps(payload).encode()
            self.assertEqual(chat(cfg["models"][0], messages, 10)["text"], "Synthetic answer")
            req = opener.return_value.open.call_args.args[0]
            self.assertEqual(json.loads(req.data)["messages"], messages)
            self.assertEqual(req.get_header("Authorization"), "Bearer synthetic-test-only")
            payload["choices"][0]["finish_reason"] = "length"
            reader.return_value = json.dumps(payload).encode()
            with self.assertRaisesRegex(ValueError, "incomplete_completion"):
                chat(cfg["models"][0], messages, 10)
        self.assertIsNone(NoRedirect().redirect_request(None))

    def test_retry_once_ten_seconds(self):
        cfg = config()
        def fail(*args):
            raise TimeoutError("secret must not escape")
        with patch("hclbench.runner.time.sleep") as sleep:
            result = execute_call(cfg["models"][0], [], cfg, transport=fail)
        sleep.assert_called_once_with(10)
        self.assertEqual(result["attempts"], 2)
        self.assertNotIn("secret", json.dumps(result))

    def test_auth_no_retry(self):
        def fail(*args):
            raise HTTPError("secret", 401, "secret", {}, None)
        cfg = config()
        result = execute_call(cfg["models"][0], [], cfg, transport=fail)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["failure_reason"], "http_401")

    def test_bad_judgment_not_unknown(self):
        with self.assertRaises(ValueError):
            parse_judge('{"judgment":"Met"}')
        cfg = config()
        cfg["retries"] = 0
        result = execute_call(cfg["models"][0], [], cfg, True, lambda *args: {"text": "not json"})
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("judgment", result)

    def test_whole_cluster_selection(self):
        dataset, _ = fixture()
        self.assertEqual(len(select_clusters(dataset, ["g2"])["cases"]), 3)
        with self.assertRaises(ValueError):
            select_clusters(dataset, ["missing"])

    @patch.dict(os.environ, {"TEST_BENCH_KEY": "synthetic-test-only"})
    def test_inference_failures_zero_without_fake_judgments(self):
        dataset, _ = fixture()
        cfg = config()
        cfg["retries"] = 0
        def fail(*args):
            raise TimeoutError()
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "run"
            summary = run(dataset, cfg, out, transport=fail)
            self.assertEqual(summary[0]["full"]["failed_cases"], 4)
            self.assertEqual(summary[0]["full"]["case_mean_100"], "0.0000")
            rows = json.loads((out / "tested-full-judgments.json").read_text())["results"]
            self.assertTrue(all(not row["judged_items"] for row in rows))

    @patch.dict(os.environ, {"TEST_BENCH_KEY": "synthetic-test-only"})
    def test_bad_credentials_never_publish_zero_score(self):
        dataset, _ = fixture()
        def fail(*args):
            raise HTTPError("secret", 401, "secret", {}, None)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "run"
            with self.assertRaises(ValueError):
                run(dataset, config(), out, transport=fail)
            self.assertFalse((out / "summary.json").exists())
            self.assertFalse((out / "tested-full-scores.json").exists())

    @patch.dict(os.environ, {"TEST_BENCH_KEY": "synthetic-test-only"})
    def test_config_safety(self):
        for url in ("http://example.com/v1", "https://other.com/v1", "https://key@example.com/v1", "https://example.com/v1?key=secret"):
            cfg = config()
            cfg["models"][0]["base_url"] = url
            with self.assertRaises(ValueError):
                check_config(cfg)
        cfg = config()
        cfg["judges"][1]["model"] = "A"
        with self.assertRaises(ValueError):
            check_config(cfg)

    @patch.dict(os.environ, {"TEST_BENCH_KEY": "synthetic-test-only"})
    def test_end_to_end_resume_and_mismatch(self):
        dataset, _ = fixture()
        cfg = config()
        calls = []
        def transport(model, messages, timeout):
            calls.append(model["id"])
            return {"text": '{"judgment":"Met","evidence":"Synthetic"}' if model["id"] != "tested" else "Synthetic answer"}
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "run"
            summary = run(dataset, cfg, out, transport=transport)
            count = len(calls)
            self.assertEqual(count, 32)
            self.assertEqual(run(dataset, cfg, out, resume=True, transport=transport), summary)
            self.assertEqual(len(calls), count)
            altered = copy.deepcopy(cfg)
            altered["models"][0]["parameters"]["max_tokens"] += 1
            with self.assertRaises(ValueError):
                run(dataset, altered, out, resume=True, transport=transport)

    @patch.dict(os.environ, {"TEST_BENCH_KEY": "synthetic-test-only"})
    def test_failed_judge_blocks_score_and_resumes(self):
        dataset, _ = fixture()
        cfg = config()
        cfg["retries"] = 0
        def bad(model, messages, timeout):
            return {"text": "Synthetic answer" if model["id"] == "tested" else "not json"}
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "run"
            with self.assertRaises(ValueError):
                run(dataset, cfg, out, transport=bad)
            self.assertFalse((out / "tested-full-scores.json").exists())
            self.assertTrue((out / "judge_failures.jsonl").exists())
            def good(model, messages, timeout):
                return {"text": '{"judgment":"Met","evidence":"Synthetic"}' if model["id"] != "tested" else "Synthetic answer"}
            run(dataset, cfg, out, resume=True, transport=good)
            self.assertTrue((out / "summary.json").exists())
