"""Block commits that contain credentials, secrets, or private infrastructure identifiers.

Runs as a git pre-commit hook (scripts/install_hooks.sh) and on demand:

    python3 scripts/check_secrets.py          # staged changes (what the next commit would contain)
    python3 scripts/check_secrets.py --all    # every tracked file

Checks:
* file names that should never be committed (.env, private keys, Terraform state/vars, CSV data, ...)
* well-known credential formats (private keys, AWS keys, GitHub/Slack/Anthropic/OpenAI/Google tokens, JWTs)
* hard-coded passwords/secrets/tokens in assignments
* real AWS account IDs in ARNs or ECR hosts (AWS documentation placeholders are allowed)
* anything listed in `.secrets-denylist` (git-ignored, one literal per line): put your real account ID,
  RDS endpoint, secret ARN, IPs, etc. there so they can never be committed without being published here.

Matches are reported by file, line, and rule only; the matched text is never printed.
Standard library only; works on Python 3.9+.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ALLOWED_ACCOUNT_IDS = {"111122223333", "123456789012", "000000000000"}  # AWS doc placeholders

FORBIDDEN_FILES: list[tuple[str, re.Pattern[str]]] = [
    ("dotenv file", re.compile(r"(^|/)\.env(\.[^/]*)?$")),
    ("private key / certificate", re.compile(r"\.(pem|p8|p12|pfx|key|jks)$", re.I)),
    ("SSH key", re.compile(r"(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$")),
    ("Terraform state", re.compile(r"\.tfstate(\.|$)")),
    ("Terraform variables", re.compile(r"\.tfvars$")),
    ("credentials file", re.compile(r"(^|/)(credentials|\.pgpass|\.netrc|\.npmrc|\.pypirc)$")),
    ("data export", re.compile(r"\.(csv|zip|parquet|sqlite3?|db|dump)$", re.I)),
]
ALLOWED_FILES = {".env.example", "infra/terraform/example.tfvars"}

CONTENT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("AWS secret access key", re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+]{40}")),
    (
        "GitHub token",
        re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}"),
    ),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI-style API key", re.compile(r"\bsk-(proj-)?[A-Za-z0-9_-]{32,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}")),
    (
        "hard-coded secret",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|api_?key|access_?token|auth_?token|client_secret)\b"
            r"\s*[:=]\s*['\"]([^'\"\s$<>{}]{8,})['\"]"
        ),
    ),
]
PLACEHOLDER_RE = re.compile(r"(?i)replace|example|placeholder|changeme|dummy|fake|local-dev|test|xxx|<|\$\{")
ACCOUNT_RE = re.compile(r"arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:(\d{12}):|\b(\d{12})\.dkr\.ecr\.")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _files(all_files: bool) -> list[str]:
    if all_files:
        return [f for f in _git("ls-files", "-z").split("\0") if f]
    return [f for f in _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0") if f]


def _content(path: str, all_files: bool) -> str | None:
    try:
        raw = (
            Path(path).read_bytes()
            if all_files
            else subprocess.run(["git", "show", f":{path}"], check=True, capture_output=True).stdout
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    if b"\0" in raw[:8000]:
        return None  # binary
    return raw.decode("utf-8", errors="replace")


def _denylist(root: Path) -> list[str]:
    path = root / ".secrets-denylist"
    if not path.exists():
        return []
    lines = (ln.strip() for ln in path.read_text(encoding="utf-8").splitlines())
    return [ln for ln in lines if ln and not ln.startswith("#")]


def scan_text(path: str, text: str, denylist: list[str]) -> list[tuple[str, int, str]]:
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if "secrets-scan: allow" in line:
            continue
        for rule, pattern in CONTENT_RULES:
            m = pattern.search(line)
            if not m:
                continue
            if rule == "hard-coded secret" and PLACEHOLDER_RE.search(m.group(2)):
                continue
            findings.append((path, lineno, rule))
        for m in ACCOUNT_RE.finditer(line):
            account = m.group(1) or m.group(2)
            if account not in ALLOWED_ACCOUNT_IDS:
                findings.append((path, lineno, "real AWS account ID"))
        for literal in denylist:
            if literal in line:
                findings.append((path, lineno, "entry from .secrets-denylist"))
    return findings


def scan_name(path: str) -> str | None:
    if path in ALLOWED_FILES or path.endswith(".example"):
        return None
    for rule, pattern in FORBIDDEN_FILES:
        if pattern.search(path):
            return rule
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan for secrets before committing.")
    parser.add_argument("--all", action="store_true", help="scan all tracked files instead of staged changes")
    args = parser.parse_args(argv)

    root = Path(_git("rev-parse", "--show-toplevel").strip())
    denylist = _denylist(root)
    findings: list[tuple[str, int, str]] = []
    files = _files(args.all)
    for path in files:
        rule = scan_name(path)
        if rule:
            findings.append((path, 0, f"forbidden file type: {rule}"))
            continue
        text = _content(str(root / path) if args.all else path, args.all)
        if text is not None:
            findings.extend(scan_text(path, text, denylist))

    scope = "tracked files" if args.all else "staged files"
    if findings:
        print(f"Secret scan FAILED ({len(findings)} finding(s) in {scope}):", file=sys.stderr)
        for path, lineno, rule in findings:
            where = f"{path}:{lineno}" if lineno else path
            print(f"  {where}  {rule}", file=sys.stderr)
        print(
            "Remove the value (use env vars / Secrets Manager), or if it is a deliberate placeholder add\n"
            "'secrets-scan: allow' on that line. Do not bypass with --no-verify.",
            file=sys.stderr,
        )
        return 1
    print(f"Secret scan passed: {len(files)} {scope}, {len(denylist)} denylist entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
