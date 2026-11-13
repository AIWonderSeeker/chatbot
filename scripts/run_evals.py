#!/usr/bin/env python3
"""Run evals/questions.jsonl against a running API and check which tools were called.

Usage: python scripts/run_evals.py --base-url http://localhost:8000
Run before changing the model, prompt, or tool descriptions. With MOCK_BEDROCK=true
this exercises the plumbing; with real Bedrock it measures tool-selection behavior.
Answers are never printed (they can contain PHI in real environments).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path


def ask(base_url: str, question: str, headers: dict[str, str]) -> dict:
    if not base_url.startswith(("http://", "https://")):
        raise SystemExit("--base-url must start with http:// or https://")
    req = urllib.request.Request(  # noqa: S310 - scheme checked above
        f"{base_url.rstrip('/')}/chat",
        data=json.dumps({"message": question}).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        return json.loads(resp.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--file", default=str(Path(__file__).parent.parent / "evals" / "questions.jsonl"))
    parser.add_argument("--header", action="append", default=[], help="Extra header, e.g. 'Cookie: ...'")
    args = parser.parse_args()
    headers = dict(h.split(": ", 1) for h in args.header)

    cases = [json.loads(line) for line in Path(args.file).read_text().splitlines() if line.strip()]
    failures = 0
    for case in cases:
        try:
            body = ask(args.base_url, case["question"], headers)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {case['id']}: {type(exc).__name__}")
            failures += 1
            continue
        called = [c["name"] for c in body.get("tool_calls", [])]
        ok = sorted(set(called)) == sorted(set(case["expect_tools"]))
        pattern = case.get("answer_must_not_match")
        if pattern and re.search(pattern, body.get("answer", "")):
            ok = False
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'} {case['id']}: expected {case['expect_tools']}, called {called}")
    print(f"\n{len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
