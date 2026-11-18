"""严格实现公开评分口径；不猜测方向，不填补未知缺失。"""
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json

MODE = "priority_weighted_with_p0_veto_and_p1_p2_penalties"
WEIGHTS = {"P0": 3, "P1": 2, "P2": 1}
Q4 = Decimal("0.0001")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def identifier(value):
    require(isinstance(value, str) and bool(value.strip()), "Expected nonempty string identifier")
    return value


def rubric_signature(items):
    require(isinstance(items, list) and bool(items), "Empty or invalid rubrics")
    seen, signature = set(), []
    for item in items:
        rid = item["id"]
        require(type(rid) in (str, int) and str(rid).strip(), "Invalid rubric id")
        require(str(rid) not in seen, "Duplicate rubric id")
        seen.add(str(rid))
        require(item["level"] in WEIGHTS, "Invalid level")
        require(item["type"] in ("positive", "negative"), "Invalid type")
        identifier(item["claim"])
        signature.append({k: item[k] for k in ("id", "level", "type", "claim")})
    require(sum(x["level"] == "P0" for x in items) <= 2, "Release constraint: at most two P0 rubrics")
    return sorted(signature, key=lambda x: str(x["id"]))


def validate_dataset(dataset):
    require(dataset["schema_version"] == "1.0", "Unsupported dataset schema")
    identifier(dataset["dataset_version"])
    cases = dataset["cases"]
    require(isinstance(cases, list) and bool(cases), "Empty dataset")
    ids = set()
    for case in cases:
        cid = identifier(case["case_id"])
        require(cid not in ids, "Duplicate case_id")
        ids.add(cid)
        identifier(case["cluster_id"])
        identifier(case["query"])
        rubric_signature(case["rubrics"])
        messages = case["messages"]
        require(isinstance(messages, list) and bool(messages), "Missing full messages")
        for message in messages:
            require(message["role"] in ("system", "user", "assistant"), "Invalid role")
            content = message["content"]
            if isinstance(content, str):
                identifier(content)
            else:
                require(isinstance(content, list) and bool(content), "Invalid content blocks")
                for block in content:
                    require(isinstance(block, dict) and set(block) == {"type", "text"}
                            and block["type"] == "text", "Unsupported content block; do not silently remove media")
                    identifier(block["text"])
        final = messages[-1]
        require(final["role"] == "user" and isinstance(final["content"], str), "Expected text final user")
        span = case.get("query_span")
        if span is None:
            require(final["content"] == case["query"], "Final user must equal explicit query")
        else:
            require(isinstance(span, dict) and set(span) == {"start", "end"}, "Invalid query span")
            start, end = span["start"], span["end"]
            require(type(start) is int and type(end) is int and 0 <= start < end <= len(final["content"]),
                    "Invalid query span bounds")
            require(final["content"][start:end] == case["query"], "Query span mismatch")
    return {x["case_id"]: x for x in cases}


def case_hash(case):
    # 同时绑定输入与 Rubrics；替换版本或回答后不得复用旧判断。
    return digest(case)


def aggregate_votes(votes, judge_ids):
    require(len(judge_ids) == 3 and len(set(judge_ids)) == 3, "Expected three distinct judges")
    require(isinstance(votes, list) and len(votes) == 3, "Expected exactly three votes")
    require(Counter(v["judge_id"] for v in votes) == Counter(judge_ids), "Wrong or duplicate judge identities")
    decisions = [v["judgment"] for v in votes]
    require(all(v in ("Met", "Not Met", "Unknown") for v in decisions), "Invalid vote")
    counts = Counter(decisions)
    return next((v for v in ("Met", "Not Met") if counts[v] >= 2), "Unresolved")


def score_case(items):
    rubric_signature(items)
    total = passed = penalty = unresolved = 0
    veto_ids, details = [], []
    for item in items:
        decision = item["decision"]
        if decision == "ReviewNeeded":
            decision = "Unresolved"
        require(decision in ("Met", "Not Met", "Unresolved"), "Invalid aggregate judgment")
        weight = WEIGHTS[item["level"]]
        expected = "Met" if item["type"] == "positive" else "Not Met"
        ok = decision == expected
        unknown = decision == "Unresolved"
        failed = not ok and not unknown
        deduction = weight if failed and item["level"] != "P0" else 0
        total += weight
        passed += weight if ok else 0
        penalty += deduction
        unresolved += unknown
        if failed and item["level"] == "P0":
            veto_ids.append(item["id"])
        details.append(dict(item, decision=decision, passed=ok, explicit_failure=failed,
                            unresolved=unknown, weight=weight, deduction=deduction))
    with localcontext() as ctx:
        ctx.prec = 50
        raw = Decimal(0) if veto_ids else Decimal(max(0, passed - penalty)) / Decimal(total)
        value = raw.quantize(Q4, rounding=ROUND_HALF_EVEN)
    reason = None
    if veto_ids:
        reason = "p0_veto"
    elif passed - penalty <= 0:
        reason = "non_positive_net"
    elif value == 0:
        reason = "rounded_to_zero"
    return {"score_raw4": str(value), "score_100": str(value * 100),
            "W": total, "A": passed, "D": penalty, "p0_vetoed": bool(veto_ids),
            "p0_failed_rubric_ids": veto_ids, "unresolved_count": unresolved,
            "net_weight_before_floor": passed - penalty,
            "floor_applied": not veto_ids and passed - penalty < 0,
            "zero_reason": reason, "rubric_details": details}


def geometric_score(scores):
    values = [Decimal(str(x)) for x in scores]
    require(bool(values), "Empty cluster")
    with localcontext() as ctx:
        ctx.prec = 50
        require(all(x.is_finite() and 0 <= x <= 1 for x in values), "Invalid case score")
        require(all(x == x.quantize(Q4, rounding=ROUND_HALF_EVEN) for x in values), "Expected four-decimal case scores")
        if any(x == 0 for x in values):
            return Decimal(0)
        if len(values) == 1:
            return values[0]
        return (sum((x.ln() for x in values), Decimal(0)) / len(values)).exp()


def evaluate(dataset, run):
    cases = validate_dataset(dataset)
    require(run["dataset_version"] == dataset["dataset_version"], "Dataset version mismatch")
    require(run["dataset_sha256"] == digest(dataset), "Dataset hash mismatch")
    for key in ("model", "run_id"):
        identifier(run[key])
    require(run["arm"] in ("full", "query"), "Invalid experiment arm")
    judges = run["judge_ids"]
    require(isinstance(judges, list) and len(judges) == 3, "Expected three judges")
    for judge in judges:
        identifier(judge)
    require(len(set(judges)) == 3, "Duplicate judges")
    rows = run["results"]
    require(isinstance(rows, list), "Invalid results")
    by_id = {x["case_id"]: x for x in rows}
    require(len(by_id) == len(rows) and set(by_id) == set(cases), "Results must cover manifest exactly; no implicit zero filling")
    scored, groups = [], defaultdict(list)
    for cid, case in cases.items():
        row = by_id[cid]
        require(row["case_sha256"] == case_hash(case), "Case hash mismatch: " + cid)
        messages = case["messages"] if run["arm"] == "full" else [{"role": "user", "content": case["query"]}]
        require(row["input_sha256"] == digest(messages), "Wrong experiment input: " + cid)
        if row["status"] == "failed":
            identifier(row["failure_reason"])
            require(not row.get("judged_items") and not row.get("answer"), "Failure contradicts answer/judgments")
            score = {"score_raw4": "0.0000", "score_100": "0.00", "zero_reason": "missing_result",
                     "unresolved_count": 0, "rubric_details": []}
        else:
            require(row["status"] == "ok", "Invalid result status")
            identifier(row["answer"])
            require(row["answer_sha256"] == digest(row["answer"]), "Answer hash mismatch")
            require(rubric_signature(row["judged_items"]) == rubric_signature(case["rubrics"]), "Rubric signature mismatch: " + cid)
            items = []
            for item in row["judged_items"]:
                decision = aggregate_votes(item["votes"], judges)
                if "final_results" in item:
                    final = item["final_results"]
                    require(("Unresolved" if final == "ReviewNeeded" else final) == decision, "Aggregate disagrees with raw votes")
                items.append(dict(item, decision=decision))
            score = score_case(items)
        score.update(case_id=cid, cluster_id=case["cluster_id"], status=row["status"])
        scored.append(score)
        groups[case["cluster_id"]].append(score)
    clusters = []
    with localcontext() as ctx:
        ctx.prec = 50
        for gid, members in groups.items():
            g = geometric_score([x["score_raw4"] for x in members])
            clusters.append({"cluster_id": gid, "member_case_ids": [x["case_id"] for x in members],
                             "expected_members": len(members), "observed_members": len(members),
                             "failed_members": sum(x["status"] == "failed" for x in members),
                             "zero_members": sum(Decimal(x["score_raw4"]) == 0 for x in members),
                             "score_0_1": str(g), "score_100": str(g * 100)})
        summary = {"case_mean_100": str(sum(Decimal(x["score_raw4"]) for x in scored) * 100 / len(scored)),
                   "cluster_mean_100": str(sum(Decimal(x["score_100"]) for x in clusters) / len(clusters)),
                   "n_expected_cases": len(cases), "n_expected_clusters": len(groups),
                   "failed_cases": sum(x["status"] == "failed" for x in scored),
                   "unresolved_rubrics": sum(x["unresolved_count"] for x in scored),
                   "zero_cases": sum(Decimal(x["score_raw4"]) == 0 for x in scored),
                   "zero_clusters": sum(Decimal(x["score_100"]) == 0 for x in clusters)}
    return {"scoring_mode": MODE, "uncertain_policy": "preserve", "dataset_sha256": digest(dataset),
            "dataset_version": dataset["dataset_version"], "run_id": run["run_id"], "model": run["model"],
            "arm": run["arm"], "judge_ids": judges, "cases": scored, "clusters": clusters, "summary": summary}


def compare(full, query):
    require(full["arm"] == "full" and query["arm"] == "query", "Expected Full and Query runs")
    for field in ("dataset_sha256", "dataset_version", "model", "scoring_mode", "uncertain_policy", "judge_ids"):
        require(full[field] == query[field], "Comparison mismatch: " + field)
    with localcontext() as ctx:
        ctx.prec = 50
        return {"model": full["model"], "full_run_id": full["run_id"], "query_run_id": query["run_id"],
                "delta_case_points": str(Decimal(full["summary"]["case_mean_100"]) - Decimal(query["summary"]["case_mean_100"])),
                "delta_cluster_points": str(Decimal(full["summary"]["cluster_mean_100"]) - Decimal(query["summary"]["cluster_mean_100"]))}
