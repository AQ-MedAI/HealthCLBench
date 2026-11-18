"""Offline preflight. Counts requests, not provider tokens or monetary cost."""
from collections import Counter
import os

from .runner import check_config
from .scoring import digest, validate_dataset


def plan(dataset, config):
    validate_dataset(dataset)
    check_config(config, require_credentials=False)
    cases = dataset["cases"]
    rubric_count = sum(len(c["rubrics"]) for c in cases)
    model_count = len(config["models"])
    inference = 2 * model_count * len(cases)
    judging = 2 * model_count * rubric_count * len(config["judges"])
    warnings = []
    if {m["model"] for m in config["models"]} & {m["model"] for m in config["judges"]}:
        warnings.append("Self-judging: tested model also appears among judges")
    if any(m["parameters"]["max_tokens"] < 8192 for m in config["judges"]):
        warnings.append("Check reasoning-model output budget: reasoning may consume max_tokens before JSON")
    if 1 in Counter(c["cluster_id"] for c in cases).values():
        warnings.append("Singleton clusters are present; they do not test within-cluster variation")
    # COSEC: only report credential presence, never values, endpoints or patient text.
    credentials = {m["api_key_env"]: bool(os.environ.get(m["api_key_env"], "").strip())
                   for m in config["models"] + config["judges"]}
    return {"dataset_sha256": digest(dataset), "cases": len(cases),
            "clusters": len({c["cluster_id"] for c in cases}), "rubrics": rubric_count,
            "levels": dict(Counter(r["level"] for c in cases for r in c["rubrics"])),
            "inference_requests": inference, "judge_requests_if_all_inference_succeeds": judging,
            "max_attempts_one_pass": (inference + judging) * (config["retries"] + 1),
            "credential_present": credentials, "warnings": warnings,
            "note": "Offline only; does not verify key validity, model availability, token limits or pricing"}
