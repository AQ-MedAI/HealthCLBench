"""Optional Hugging Face input adapter; no upload or remote code execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

from .scoring import require, validate_dataset
from .dataset_io import decode_dataset

MAX_BYTES = 256 * 1024 * 1024


def check_source(config):
    require(isinstance(config, dict) and set(config) ==
            {"repo_id", "revision", "filename", "sha256"}, "Invalid HF source configuration")
    require(all(isinstance(v, str) and v for v in config.values()),
            "Fill repo_id, revision, filename and sha256 before downloading")
    require(re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", config["repo_id"]) is not None
            and ".." not in config["repo_id"], "Expected namespace/dataset repository ID")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", config["revision"]) is not None
            and ".." not in config["revision"], "Invalid revision")
    name = config["filename"]
    # COSEC: only repository-relative JSON paths, never URLs or traversal paths.
    require(not PurePosixPath(name).is_absolute() and
            all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in (".", "..")
                for part in name.split("/")) and name.endswith((".json", ".jsonl", ".csv")), "Invalid data filename")
    require(re.fullmatch(r"[0-9a-f]{64}", config["sha256"]) is not None,
            "Expected lowercase SHA-256 of the downloaded file bytes")


def download(config, output, *, token_env=None, local_files_only=False, fetch=None):
    check_source(config)
    output = Path(output)
    require(not config["filename"].endswith(".jsonl") or output.suffix == ".jsonl",
            "JSONL downloads require a .jsonl output path")
    require(not config["filename"].endswith(".csv") or output.suffix == ".csv",
            "CSV downloads require a .csv output path")
    require(not output.exists() and not output.is_symlink(), "Output already exists")
    token = False  # Do not implicitly send a locally stored HF credential.
    if token_env is not None:
        require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env) is not None,
                "Invalid token environment variable name")
        token = os.environ.get(token_env)
        require(bool(token), "Requested HF token environment variable is empty")
    if fetch is None:
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            raise ValueError('Install the optional dependency: pip install ".[hf]"') from None
        fetch = hf_hub_download
    # COSEC: endpoint fixed to the official Hub; no custom URL or remote Python
    # dataset scripts. The official SDK handles Hub/CDN download and caching.
    try:
        path = Path(fetch(repo_id=config["repo_id"], repo_type="dataset",
                          revision=config["revision"], filename=config["filename"],
                          endpoint="https://huggingface.co", token=token,
                          local_files_only=local_files_only))
    except Exception:
        raise ValueError("HF download failed; check repository, revision, access and network") from None
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "Dataset exceeds 256 MiB validation limit")
    require(hashlib.sha256(raw).hexdigest() == config["sha256"], "Dataset file SHA-256 mismatch")
    dataset = decode_dataset(raw, jsonl=config["filename"].endswith(".jsonl"),
                             csv_format=config["filename"].endswith(".csv"))
    cases = validate_dataset(dataset)
    # COSEC: exclusive creation, private file permissions, no overwrite; retain
    # exact downloaded bytes so the published file checksum remains valid.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
    return {"cases": len(cases), "clusters": len({c["cluster_id"] for c in cases.values()}),
            "rubrics": sum(len(c["rubrics"]) for c in cases.values()),
            "sha256": config["sha256"], "dataset_version": dataset["dataset_version"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True, help="New dataset CSV/JSONL/JSON; matching suffix, parent must exist")
    parser.add_argument("--token-env", help="Explicit environment variable for private/gated Hub access")
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args()
    try:
        result = download(json.loads(Path(args.config).read_text()), args.output,
                          token_env=args.token_env, local_files_only=args.local_files_only)
    except (ValueError, OSError, KeyError, TypeError):
        parser.exit(2, "Download/validation failed. Check HF configuration, optional dependency, "
                    "access, checksum and unused output path; no evaluation was started.\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
