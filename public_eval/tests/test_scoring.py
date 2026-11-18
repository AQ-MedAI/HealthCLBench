import copy
from decimal import Decimal
import unittest

from hclbench.scoring import (aggregate_votes, case_hash, compare, digest,
                             evaluate, geometric_score, score_case, validate_dataset)
from hclbench.__main__ import collect, judge_requests, prepare


def rubric(rid=1, level="P1", kind="positive", decision="Met"):
    return dict(id=rid, level=level, type=kind, claim=f"Synthetic criterion {rid}", decision=decision)


def fixture():
    cases = []
    for cid, gid in (("a", "g1"), ("b", "g2"), ("c", "g2"), ("d", "g2")):
        cases.append(dict(case_id=cid, cluster_id=gid, query="Synthetic question?",
                          messages=[dict(role="user", content="Synthetic question?")],
                          rubrics=[{k: v for k, v in rubric().items() if k != "decision"}]))
    dataset = dict(schema_version="1.0", dataset_version="synthetic-v1", cases=cases)
    rows = []
    for case in cases:
        item = dict(case["rubrics"][0], votes=[dict(judge_id=j, judgment="Met") for j in ("A", "B", "C")])
        rows.append(dict(case_id=case["case_id"], case_sha256=case_hash(case), input_sha256=digest(case["messages"]),
                         status="ok", answer="Synthetic answer", answer_sha256=digest("Synthetic answer"), judged_items=[item]))
    run = dict(dataset_version=dataset["dataset_version"], dataset_sha256=digest(dataset),
               run_id="run-1", model="synthetic", arm="full", judge_ids=["A", "B", "C"], results=rows)
    return dataset, run


class ScoringTests(unittest.TestCase):
    def test_six_direction_states(self):
        for kind in ("positive", "negative"):
            for decision in ("Met", "Not Met", "Unresolved"):
                with self.subTest(kind=kind, decision=decision):
                    result = score_case([rubric(kind=kind, decision=decision)])
                    ok = decision == ("Met" if kind == "positive" else "Not Met")
                    self.assertEqual(result["score_raw4"], "1.0000" if ok else "0.0000")
                    self.assertEqual(result["D"], 0 if ok or decision == "Unresolved" else 2)

    def test_examples(self):
        items = [rubric(1, "P0"), rubric(2, "P1"), rubric(3, "P2")]
        self.assertEqual(score_case(items)["score_raw4"], "1.0000")
        items[1]["decision"] = "Not Met"
        self.assertEqual(score_case(items)["score_raw4"], "0.3333")
        items[1]["decision"] = "Met"
        items[2]["decision"] = "Not Met"
        self.assertEqual(score_case(items)["score_raw4"], "0.6667")

    def test_veto_both_directions(self):
        for kind, decision in (("positive", "Not Met"), ("negative", "Met")):
            result = score_case([rubric(1, "P0", kind, decision), rubric(2)])
            self.assertTrue(result["p0_vetoed"])
            self.assertEqual(result["score_raw4"], "0.0000")

    def test_unresolved_p0(self):
        result = score_case([rubric(1, "P0", decision="ReviewNeeded"), rubric(2)])
        self.assertEqual(result["score_raw4"], "0.4000")
        self.assertFalse(result["p0_vetoed"])

    def test_floor(self):
        result = score_case([rubric(1, decision="Not Met"), rubric(2, "P2")])
        self.assertTrue(result["floor_applied"])
        result = score_case([rubric(1, decision="Not Met"), rubric(2)])
        self.assertFalse(result["floor_applied"])
        self.assertEqual(result["zero_reason"], "non_positive_net")

    def test_half_even(self):
        # 1/32 is exactly .03125 -> .0312; 3/32 -> .09375 -> .0938.
        for passes, expected in ((1, "0.0312"), (3, "0.0938")):
            items = [rubric(i, "P2", decision="Met" if i < passes else "Unresolved") for i in range(32)]
            self.assertEqual(score_case(items)["score_raw4"], expected)

    def test_votes(self):
        for votes, expected in ((["Met", "Met", "Unknown"], "Met"),
                                (["Met", "Unknown", "Unknown"], "Unresolved"),
                                (["Met", "Not Met", "Unknown"], "Unresolved"),
                                (["Not Met", "Not Met", "Unknown"], "Not Met")):
            self.assertEqual(aggregate_votes([dict(judge_id=j, judgment=v) for j, v in zip("ABC", votes)], list("ABC")), expected)
        with self.assertRaises(ValueError):
            aggregate_votes([dict(judge_id="A", judgment="Met")] * 3, list("ABC"))

    def test_geo(self):
        self.assertEqual(geometric_score([1, 0, 1]), 0)
        self.assertEqual(geometric_score(["0.1234"]), Decimal("0.1234"))
        self.assertEqual(round(geometric_score([1, 1, "0.5556", 1]) * 100, 2), Decimal("86.34"))
        for invalid in ([], ["NaN"], ["Infinity"], [-1], [2], ["0.12345"]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                geometric_score(invalid)

    def test_empty_and_invalid_rubrics(self):
        for items in ([], [rubric(), rubric()], [rubric(level="P3")], [rubric(kind="bad")], [rubric(decision="Unknown")]):
            with self.assertRaises(ValueError):
                score_case(items)

    def test_complete_run_and_failure(self):
        dataset, run = fixture()
        self.assertEqual(Decimal(evaluate(dataset, run)["summary"]["cluster_mean_100"]), 100)
        run["results"][1].update(status="failed", answer="", judged_items=[], failure_reason="execution_failed")
        result = evaluate(dataset, run)
        self.assertEqual(Decimal(result["summary"]["cluster_mean_100"]), 50)
        self.assertEqual(result["summary"]["failed_cases"], 1)
        run["results"].pop()
        with self.assertRaises(ValueError):
            evaluate(dataset, run)

    def test_cluster_equal_weight(self):
        dataset, run = fixture()
        for case, row in zip(dataset["cases"], run["results"]):
            case["rubrics"] = [{k: v for k, v in rubric(i, "P2").items() if k != "decision"} for i in range(4)]
            row["case_sha256"] = case_hash(case)
            row["judged_items"] = [dict(r, votes=[dict(judge_id=j, judgment="Met" if case["case_id"] == "a" or r["id"] == 0 else "Unknown") for j in "ABC"]) for r in case["rubrics"]]
        run["dataset_sha256"] = digest(dataset)
        summary = evaluate(dataset, run)["summary"]
        self.assertEqual(Decimal(summary["case_mean_100"]), Decimal("43.75"))
        self.assertAlmostEqual(Decimal(summary["cluster_mean_100"]), Decimal("62.50"), places=40)

    def test_tampering(self):
        mutations = [lambda r: r.update(dataset_version="wrong"),
                     lambda r: r["results"].append(r["results"][0]),
                     lambda r: r["results"][0].update(input_sha256="bad"),
                     lambda r: r["results"][0].update(answer="changed"),
                     lambda r: r["results"][0]["judged_items"][0].update(claim="changed"),
                     lambda r: r["results"][0]["judged_items"][0].update(final_results="Not Met")]
        for mutate in mutations:
            dataset, run = fixture()
            mutate(run)
            with self.assertRaises(ValueError):
                evaluate(dataset, run)

    def test_prepare_no_rubric_leak(self):
        dataset, _ = fixture()
        result = prepare(dataset, "query")
        self.assertNotIn("rubrics", result["requests"][0])
        self.assertEqual(len(result["requests"][0]["messages"]), 1)

    def test_collect_and_compare(self):
        dataset, run = fixture()
        predictions = copy.deepcopy(run)
        for row in predictions["results"]:
            row.pop("judged_items")
        req = judge_requests(dataset, predictions, list("ABC"))
        responses = [dict(r, judgment="Met") for r in req["requests"]]
        collected = collect(dataset, predictions, responses, list("ABC"))
        full = evaluate(dataset, collected)
        collected["arm"] = "query"
        query = evaluate(dataset, collected)
        self.assertEqual(Decimal(compare(full, query)["delta_cluster_points"]), 0)
        responses.pop()
        with self.assertRaises(ValueError):
            collect(dataset, predictions, responses, list("ABC"))


if __name__ == "__main__":
    unittest.main()
