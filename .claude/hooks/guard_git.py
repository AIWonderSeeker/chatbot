"""Claude Code PreToolUse hook (Bash): guard git commits and pushes in this public repo.

* blocks `git commit --no-verify` (it would skip the pre-commit secret scan) and force pushes
* runs scripts/check_secrets.py before `git commit` (staged files) and `git push` (all tracked files)

Only real git invocations count: the command is tokenized with shlex, heredoc bodies are
dropped, and each `;` / `&&` / `||` / `|` segment is checked separately, so text that merely
mentions "git push" (a commit message, a file being written) does not trigger it.

Exit code 2 blocks the tool call and shows stderr to Claude; 0 lets it run.
Reads the hook payload (JSON) on stdin. Standard library only; Python 3.9+.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_OPERATORS = {";", "&&", "||", "|", "&", "\n", "(", ")"}
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def git_invocations(command: str) -> list[tuple[str, list[str]]]:
    """Return (subcommand, args) for each git call in a shell command line."""
    text = _HEREDOC.sub("", command)
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        tokens = text.split()
    calls: list[tuple[str, list[str]]] = []
    segment: list[str] = []
    for tok in [*tokens, ";"]:
        if tok in _OPERATORS or set(tok) <= set(";&|()\n"):
            while segment and _ENV_ASSIGN.match(segment[0]):
                segment.pop(0)
            if segment and Path(segment[0]).name == "git":
                args = segment[1:]
                i = 0
                while i < len(args) and args[i].startswith("-"):  # global options like -C <dir>
                    i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
                if i < len(args):
                    calls.append((args[i], args[i + 1 :]))
            segment = []
        else:
            segment.append(tok)
    return calls


def check(command: str) -> tuple[int, str]:
    calls = git_invocations(command)
    commits = [a for sub, a in calls if sub == "commit"]
    pushes = [a for sub, a in calls if sub == "push"]
    for args in commits:
        short = [a for a in args if a.startswith("-") and not a.startswith("--")]
        if "--no-verify" in args or any("n" in a[1:] and a != "-m" for a in short if len(a) <= 4):
            return 2, "Blocked: --no-verify would skip the secret scan. Fix the finding instead."
    for args in pushes:
        if any(a in ("-f", "--force", "--force-with-lease") or a.startswith(("+", "--force")) for a in args):
            return 2, "Blocked: force push is not allowed from Claude in this repo; ask the user."
    if not (commits or pushes):
        return 0, ""
    scan = [sys.executable, str(ROOT / "scripts" / "check_secrets.py")]
    if pushes:
        scan.append("--all")
    result = subprocess.run(scan, cwd=ROOT, capture_output=True, text=True)  # noqa: S603
    if result.returncode != 0:
        return 2, result.stderr or result.stdout
    return 0, ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    code, message = check(str(payload.get("tool_input", {}).get("command", "")))
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
