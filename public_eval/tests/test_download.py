import hashlib
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from hclbench.download import check_source, download
from hclbench.dataset_io import to_jsonl, load_dataset
from test_scoring import fixture


class DownloadTests(unittest.TestCase):
    def test_labeled_csv_download_and_invalid_inputs(self):
        dataset, _ = fixture()
        rows = []
        for i, case in enumerate(dataset["cases"], 1):
            messages = [dict(role="user", content="<current_user_message>\nquestion\n</current_user_message>")]
            rows.append(dict(num=str(i), messages=json.dumps(messages),
                             rubrics=json.dumps(case["rubrics"]), label='{"medical_scenario":"test"}',
                             cluster_id="1"))
        def encode():
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            return stream.getvalue().encode("utf-8-sig")
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp) / "cache", Path(tmp) / "data.csv"
            raw = encode()
            source.write_bytes(raw)
            cfg = dict(self.config, filename="HealthCLBench.csv", sha256=hashlib.sha256(raw).hexdigest())
            download(cfg, out, fetch=Mock(return_value=source))
            self.assertEqual(out.read_bytes(), raw)
            loaded = load_dataset(out)
            self.assertEqual(len(loaded["cases"]), 4)
            self.assertEqual(loaded["cases"][0]["query"], "question")
            self.assertEqual(loaded["cases"][0]["label"], {"medical_scenario": "test"})
            with self.assertRaises(ValueError):
                download(cfg, Path(tmp) / "wrong.jsonl", fetch=Mock(return_value=source))
            from hclbench.dataset_io import from_csv
            for key, value in (("num", "2"), ("label", "[]"),
                               ("messages", '[{"role":"user","content":"no markers"}]'),
                               ("rubrics", "not json")):
                old = rows[0][key]
                rows[0][key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    from_csv(encode())
                rows[0][key] = old

    def test_jsonl_download(self):
        dataset, _ = fixture()
        raw = to_jsonl(dataset)
        cfg = dict(self.config, filename="HealthCLBench.jsonl", sha256=hashlib.sha256(raw).hexdigest())
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cache"
            p.write_bytes(raw)
            out = Path(tmp) / "data.jsonl"
            download(cfg, out, fetch=Mock(return_value=p))
            self.assertEqual(load_dataset(out), dataset)
            with self.assertRaises(ValueError):
                download(cfg, Path(tmp) / "wrong.json", fetch=Mock(return_value=p))

    def setUp(self):
        data, _ = fixture()
        self.raw = json.dumps(data).encode()
        self.config = dict(repo_id="example/benchmark", revision="v1", filename="dataset.json",
                           sha256=hashlib.sha256(self.raw).hexdigest())

    def test_download_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "cached.json", Path(temp) / "dataset.json"
            source.write_bytes(self.raw)
            fetch = Mock(return_value=source)
            result = download(self.config, output, fetch=fetch, local_files_only=True)
            self.assertEqual(output.read_bytes(), self.raw)
            self.assertEqual(result["cases"], 4)
            self.assertFalse(fetch.call_args.kwargs["token"])
            self.assertEqual(fetch.call_args.kwargs["endpoint"], "https://huggingface.co")
            self.assertEqual(fetch.call_args.kwargs["repo_type"], "dataset")
            self.assertTrue(fetch.call_args.kwargs["local_files_only"])
            with self.assertRaises(ValueError):
                download(self.config, output, fetch=fetch)
            self.assertEqual(fetch.call_count, 1)

    def test_bad_sources_before_network(self):
        for key, value in (("repo_id", ""), ("repo_id", "https://bad.test/x"),
                           ("revision", "../main"), ("filename", "../data.json"),
                           ("filename", "/data.json"), ("filename", "a//data.json"),
                           ("filename", "data.py"), ("sha256", "")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                check_source(dict(self.config, **{key: value}))

    def test_invalid_bytes_never_create_output(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "cache", Path(temp) / "out"
            source.write_bytes(b'{}')
            for config in (self.config, dict(self.config, sha256=hashlib.sha256(b'{}').hexdigest())):
                with self.assertRaises((ValueError, KeyError)):
                    download(config, output, fetch=Mock(return_value=source))
                self.assertFalse(output.exists())

    @patch.dict(os.environ, {"TEST_HF_KEY": "synthetic-only"})
    def test_explicit_token_and_safe_errors(self):
        fetch = Mock(side_effect=RuntimeError("synthetic-only"))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "HF download failed") as error:
                download(self.config, Path(temp) / "out", token_env="TEST_HF_KEY", fetch=fetch)
            self.assertNotIn("synthetic-only", str(error.exception))
            self.assertEqual(fetch.call_args.kwargs["token"], "synthetic-only")
