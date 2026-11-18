import copy
import json
import unittest

from hclbench.import_snapshot import convert
from hclbench.__main__ import prepare
from hclbench.scoring import validate_dataset


def source():
    ext = {"child_case_id": "synthetic-S1", "parent_case_id": "synthetic",
           "extParams": {"evePersonalInfo": {"synthetic": True}, "eveAdditionalInfo": [{"messages": [
               {"role": "user", "content": [{"type": "text", "text": "Synthetic history"}]},
               {"role": "assistant", "content": "Synthetic reply"},
               {"role": "user", "content": "<background_context>Evidence</background_context>\n<current_user_message>\n问题？\n</current_user_message>"}]}]},
           "rubrics": [{"id": 1, "level": "P0", "type": "positive", "claim": "Synthetic criterion"}]}
    return {"columns": ["num", "ext_info"], "rows": [[1, json.dumps(ext, ensure_ascii=False)]]}, ext


class ImportTests(unittest.TestCase):
    def test_lossless_full_and_explicit_query(self):
        snapshot, ext = source()
        before = copy.deepcopy(snapshot)
        data, provenance, report = convert(snapshot, "synthetic-v1")
        self.assertEqual(snapshot, before)
        case = data["cases"][0]
        self.assertEqual(case["messages"], ext["extParams"]["eveAdditionalInfo"][0]["messages"])
        self.assertEqual(case["rubrics"], ext["rubrics"])
        self.assertEqual(prepare(data, "full")["requests"][0]["messages"], case["messages"])
        self.assertEqual(prepare(data, "query")["requests"][0]["messages"], [{"role": "user", "content": "问题？"}])
        self.assertIn("evePersonalInfo", provenance[0]["runtime_context_not_rendered"])
        self.assertFalse(report["release_ready"])

    def test_invalid_spans(self):
        data, _, _ = convert(source()[0], "synthetic-v1")
        for span in ({"start": -1, "end": 3}, {"start": True, "end": 3}, {"start": 0, "end": 3}):
            changed = copy.deepcopy(data)
            changed["cases"][0]["query_span"] = span
            with self.assertRaises(ValueError):
                validate_dataset(changed)

    def test_ambiguous_query_or_duplicate_num_rejected(self):
        snap, ext = source()
        snap["rows"] *= 2
        with self.assertRaises(ValueError):
            convert(snap, "synthetic-v1")
        ext["extParams"]["eveAdditionalInfo"][0]["messages"][-1]["content"] += "<current_user_message>Other</current_user_message>"
        with self.assertRaises(ValueError):
            convert({"columns": ["num", "ext_info"], "rows": [[1, json.dumps(ext)]]}, "synthetic-v1")

    def test_media_never_silently_dropped(self):
        data, _, _ = convert(source()[0], "synthetic-v1")
        data["cases"][0]["messages"][0]["content"].append({"type": "image_url", "image_url": {"url": "synthetic"}})
        with self.assertRaises(ValueError):
            validate_dataset(data)

    def test_invalid_arm_rejected(self):
        data, _, _ = convert(source()[0], "synthetic-v1")
        with self.assertRaises(ValueError):
            prepare(data, "typo")
