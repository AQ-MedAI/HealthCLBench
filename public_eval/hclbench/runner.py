"""OpenAI-compatible batch runner. Trusted operator config; no dataset URLs fetched."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from .__main__ import collect, judge_requests, prepare, JUDGE_PROTOCOL
from .scoring import compare, digest, evaluate, require, validate_dataset
from .dataset_io import load_dataset

RUNNER_VERSION = "1"


def save(path, value):
    # COSEC: checkpoints use generated names, private permissions and atomic replacement.
    require(not path.is_symlink(), "Refuse symlink output")
    if path.exists():
        require(json.loads(path.read_text()) == value, "Existing output differs; use a new run directory")
        return
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def check_config(config, require_credentials=True):
    require(set(config) == {"models", "judges", "workers", "timeout_seconds", "retries", "retry_delay_seconds"}, "Unexpected config fields")
    for key, low, high in (("workers", 1, 32), ("timeout_seconds", 1, 3600), ("retries", 0, 3), ("retry_delay_seconds", 0, 60)):
        require(type(config[key]) is int and low <= config[key] <= high, "Invalid " + key)
    require(isinstance(config["models"], list) and bool(config["models"]), "Missing models")
    require(isinstance(config["judges"], list) and len(config["judges"]) == 3, "Three judges required")
    for group in (config["models"], config["judges"]):
        require(len({m["id"] for m in group}) == len(group), "Duplicate identity")
        require(len({(m["base_url"], m["model"]) for m in group}) == len(group), "Duplicate model under different labels")
        for model in group:
            require(set(model) == {"id", "model", "base_url", "allowed_host", "api_key_env", "parameters"}, "Unexpected model config")
            require(re.fullmatch(r"[A-Za-z0-9_-]+", model["id"]) is not None, "Unsafe model id")
            require(isinstance(model["model"], str) and bool(model["model"].strip()), "Missing model name")
            url = urlsplit(model["base_url"])
            # COSEC: operator-approved exact host, HTTPS, no credentials/query/redirect.
            # Explicit allowlist supports private gateways; config is trusted, not an HTTP API input.
            require(url.scheme == "https" and url.hostname == model["allowed_host"] and url.hostname
                    and not url.username and not url.password and not url.query and not url.fragment,
                    "Endpoint is not allowlisted HTTPS")
            require(re.fullmatch(r"[A-Z][A-Z0-9_]*", model["api_key_env"]) is not None, "Invalid key env name")
            if require_credentials:
                require(bool(os.environ.get(model["api_key_env"], "").strip()), "Missing credential environment variable")
            p = model["parameters"]
            require(isinstance(p, dict) and set(p) <= {"temperature", "max_tokens", "top_p", "seed"}, "Unsupported generation parameters")
            require(type(p.get("max_tokens")) is int and p["max_tokens"] > 0, "Set max_tokens explicitly")
            if "temperature" in p:
                require(type(p["temperature"]) in (int, float) and 0 <= p["temperature"] <= 2, "Invalid temperature")
            if "top_p" in p:
                require(type(p["top_p"]) in (int, float) and 0 < p["top_p"] <= 1, "Invalid top_p")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def chat(model, messages, timeout):
    body = {"model": model["model"], "messages": messages, **model["parameters"], "stream": False}
    request = Request(model["base_url"].rstrip("/") + "/chat/completions",
                      data=json.dumps(body, ensure_ascii=False).encode(),
                      headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ[model["api_key_env"]]})
    # COSEC: don't inherit HTTP proxies or forward Authorization across redirects.
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    require(len(raw) <= 4 * 1024 * 1024, "response_too_large")
    data = json.loads(raw)
    choice = data["choices"][0]
    require(choice.get("finish_reason") == "stop", "incomplete_completion")
    text = choice["message"]["content"]
    require(isinstance(text, str) and bool(text.strip()), "empty_completion")
    return {"text": text, "usage": data.get("usage", {}), "response_model": data.get("model"),
            "finish_reason": choice["finish_reason"]}


def parse_judge(text):
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    obj = json.loads(text)
    require(isinstance(obj, dict) and obj.get("judgment") in ("Met", "Not Met", "Unknown"), "invalid_judge_json")
    require(isinstance(obj.get("evidence"), str), "missing_judge_evidence")
    return {"judgment": obj["judgment"], "evidence": obj["evidence"]}


def execute_call(model, messages, config, is_judge=False, transport=chat):
    start = time.monotonic()
    for attempt in range(config["retries"] + 1):
        retryable = True
        try:
            response = transport(model, messages, config["timeout_seconds"])
            parsed = parse_judge(response["text"]) if is_judge else {}
            return dict(response, **parsed, status="ok", attempts=attempt+1, elapsed_seconds=round(time.monotonic()-start, 3))
        except HTTPError as exc:
            # COSEC: never log response body, authorization, URL or raw exception message.
            error = "http_" + str(exc.code)
            retryable = exc.code in (408, 429, 500, 502, 503, 504)
        except (TimeoutError, URLError, ConnectionError, OSError):
            error = "transport_error"
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            safe_codes = {"incomplete_completion", "empty_completion", "response_too_large",
                          "invalid_judge_json", "missing_judge_evidence"}
            error = str(exc) if type(exc) is ValueError and str(exc) in safe_codes else "invalid_or_incomplete_response"
        if not retryable or attempt == config["retries"]:
            return {"status": "failed", "failure_reason": error, "attempts": attempt+1,
                    "elapsed_seconds": round(time.monotonic()-start, 3)}
        time.sleep(config["retry_delay_seconds"])


def select_clusters(dataset, cluster_ids):
    validate_dataset(dataset)
    if not cluster_ids:
        return dataset
    wanted = set(cluster_ids)
    require(wanted <= {c["cluster_id"] for c in dataset["cases"]}, "Unknown cluster id")
    return dict(dataset, cases=[c for c in dataset["cases"] if c["cluster_id"] in wanted])


def run(dataset, config, out, resume=False, transport=chat):
    check_config(config)
    validate_dataset(dataset)
    out = Path(out)
    if out.exists():
        require(resume, "Output exists; use --resume or a new directory")
    else:
        out.mkdir(parents=True, mode=0o700)
    require(not out.is_symlink(), "Refuse symlink run directory")
    lock = out / ".lock"
    with lock.open("x"):
        pass
    try:
        meta = {"runner_version": RUNNER_VERSION, "dataset_sha256": digest(dataset),
                "config": config, "judge_protocol_sha256": digest(JUDGE_PROTOCOL),
                "paper_prompt_parity": False, "self_judging_possible": bool(
                    {m["model"] for m in config["models"]} & {m["model"] for m in config["judges"]})}
        save(out / "manifest.json", meta)
        save(out / "dataset.json", dataset)
        cache = out / "calls"
        cache.mkdir(exist_ok=True, mode=0o700)
        require(not cache.is_symlink(), "Refuse symlink cache")
        def call(job):
            model, request, is_judge, scope = job
            key = digest({"model": model, "request": request, "judge": is_judge, "scope": scope, "runner": RUNNER_VERSION})
            path = cache / (key + ".json")
            require(not path.is_symlink(), "Refuse symlink checkpoint")
            if path.exists():
                result = json.loads(path.read_text())
                require(result["request_sha256"] == key, "Checkpoint identity mismatch")
                return result
            result = execute_call(model, request["messages"], config, is_judge, transport)
            result["request_sha256"] = key
            # Failed inference is a finalized zero; failed grading blocks scoring and
            # is retried on explicit resume. Successful calls are never repeated.
            if result["status"] == "ok" or not is_judge:
                save(path, result)
            return result
        summary = []
        with ThreadPoolExecutor(max_workers=config["workers"]) as pool:
            for model in config["models"]:
                pair = {}
                for arm in ("full", "query"):
                    prefix = model["id"] + "-" + arm
                    manifest = prepare(dataset, arm)
                    requests = manifest["requests"]
                    print(json.dumps({"run": prefix, "phase": "inference", "requests": len(requests)}), flush=True)
                    generated = list(pool.map(call, [(model, r, False, prefix) for r in requests]))
                    require(not any(a.get("failure_reason") in ("http_401", "http_403") for a in generated),
                            "Credential/permission failure: do not publish model scores")
                    predictions = {k: manifest[k] for k in ("dataset_version", "dataset_sha256", "arm")}
                    predictions.update(model=model["model"], run_id=prefix, results=[])
                    for req, answer in zip(requests, generated):
                        row = {k: req[k] for k in ("case_id", "case_sha256", "input_sha256")}
                        row["status"] = answer["status"]
                        row.update({"answer": answer["text"]} if answer["status"] == "ok" else {"failure_reason": answer["failure_reason"]})
                        predictions["results"].append(row)
                    save(out / (prefix + "-predictions.json"), predictions)
                    judges = {j["id"]: j for j in config["judges"]}
                    jr = judge_requests(dataset, predictions, list(judges))
                    print(json.dumps({"run": prefix, "phase": "judging", "requests": len(jr["requests"])}), flush=True)
                    answers = list(pool.map(call, [(judges[r["judge_id"]], r, True, prefix) for r in jr["requests"]]))
                    errors = [dict(case_id=r["case_id"], rubric_id=r["rubric_id"], judge_id=r["judge_id"],
                                   failure_reason=a["failure_reason"], attempts=a["attempts"],
                                   elapsed_seconds=a["elapsed_seconds"]) for r, a in zip(jr["requests"], answers) if a["status"] != "ok"]
                    if errors:
                        # Append-only diagnostics; never turn a technical failure into Unknown.
                        with (out / "judge_failures.jsonl").open("a") as stream:
                            stream.write(json.dumps({"run": prefix, "errors": errors}) + "\n")
                        pair[arm] = None
                        print(json.dumps({"blocked": prefix, "failed_judge_calls": len(errors)}), flush=True)
                        continue
                    responses = []
                    for req, answer in zip(jr["requests"], answers):
                        row = {k: req[k] for k in ("case_id", "case_sha256", "answer_sha256", "rubric_id", "judge_id")}
                        row.update(judgment=answer["judgment"], evidence=answer["evidence"])
                        responses.append(row)
                    judgment = collect(dataset, predictions, responses, list(judges))
                    pair[arm] = evaluate(dataset, judgment)
                    save(out / (prefix + "-judgments.json"), judgment)
                    save(out / (prefix + "-scores.json"), pair[arm])
                    print(json.dumps({"completed": prefix, **pair[arm]["summary"]}), flush=True)
                if all(pair.values()):
                    summary.append({"model": model["model"], "full": pair["full"]["summary"],
                                    "query": pair["query"]["summary"], "delta": compare(pair["full"], pair["query"])})
                else:
                    summary.append({"model": model["model"], "incomplete": True,
                                    "full": pair["full"]["summary"] if pair["full"] else None,
                                    "query": pair["query"]["summary"] if pair["query"] else None})
        if any(s.get("incomplete") for s in summary):
            save(out / ("partial-" + digest(summary)[:16] + ".json"), summary)
            raise ValueError("Incomplete judging: partial results saved; no complete summary published")
        save(out / "summary.json", summary)
        return summary
    finally:
        lock.unlink()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--clusters", nargs="+")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="Offline validation and request counts; no network or writes")
    a = p.parse_args()
    try:
        data = select_clusters(load_dataset(a.dataset), a.clusters)
        config = json.loads(Path(a.config).read_text())
        if a.dry_run:
            from .planning import plan
            print(json.dumps(plan(data, config), ensure_ascii=False, indent=2))
        else:
            run(data, config, a.output_dir, a.resume)
    except (ValueError, KeyError, TypeError, OSError):
        # CLI deliberately avoids dumping credentials or malformed private data.
        raise SystemExit("Run stopped: check config, credentials, existing run identity and judge_failures.jsonl")


if __name__ == "__main__":
    main()
