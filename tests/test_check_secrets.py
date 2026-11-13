"""scripts/check_secrets.py catches credentials and private identifiers.

Sample secrets are assembled at runtime so this file never contains a literal the
scanner (or GitHub's secret scanning) would flag.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_secrets.py"
_spec = importlib.util.spec_from_file_location("check_secrets", _SCRIPT)
cs = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cs
_spec.loader.exec_module(cs)


def _rules(text: str, denylist: list[str] | None = None) -> set[str]:
    return {rule for _, _, rule in cs.scan_text("f.py", text, denylist or [])}


@pytest.mark.parametrize(
    "text, rule",
    [
        ("-----BEGIN " + "RSA PRIVATE KEY-----", "private key block"),
        ("key = " + "AKIA" + "ABCDEFGHIJKLMNOP", "AWS access key id"),
        ("token: " + "ghp_" + "a1B2" * 9, "GitHub token"),
        ("ANTHROPIC_API_KEY=" + "sk-ant-" + "api03-" + "x7" * 12, "Anthropic API key"),
        ('password = "' + "Tr0ub4dor&3xyz" + '"', "hard-coded secret"),
        ("arn:aws:iam::" + "4" * 12 + ":role/x", "real AWS account ID"),
        ("image: " + "9" * 12 + ".dkr.ecr.us-east-1.amazonaws.com/api", "real AWS account ID"),
    ],
)
def test_detects_secrets(text, rule):
    assert rule in _rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "arn:aws:secretsmanager:us-east-1:111122223333:secret:x",
        'password = "REPLACE_ME_LATER"',
        "MCP_AUTH_TOKEN=local-dev-token",
        'client_secret = "${var.secret}"',
        'password = "' + "Tr0ub4dor&3xyz" + '"  # secrets-scan: allow',
        "SNOWFLAKE_PASSWORD=",
    ],
)
def test_placeholders_pass(text):
    assert _rules(text) == set()


def test_denylist_literals_blocked():
    assert "entry from .secrets-denylist" in _rules("host = my-db.private-id.rds", ["private-id"])


@pytest.mark.parametrize(
    "path, blocked",
    [
        (".env", True),
        ("config/.env.prod", True),
        (".env.example", False),
        ("global-bundle.pem", True),
        ("keys/id_ed25519", True),
        ("infra/terraform/terraform.tfstate", True),
        ("infra/terraform/prod.tfvars", True),
        ("infra/terraform/example.tfvars", False),
        ("data/fake/claims.csv", True),
        ("app/main.py", False),
    ],
)
def test_forbidden_file_names(path, blocked):
    assert (cs.scan_name(path) is not None) is blocked


def test_hook_blocks_a_staged_secret(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True)

    git("init", "-q")
    (tmp_path / "ok.py").write_text("x = 1\n")
    (tmp_path / "bad.py").write_text("key = '" + "AKIA" + "QRSTUVWXYZ234567" + "'\n")
    git("add", "ok.py")
    ok = subprocess.run([sys.executable, str(_SCRIPT)], cwd=tmp_path, capture_output=True, text=True)
    assert ok.returncode == 0
    git("add", "bad.py")
    bad = subprocess.run([sys.executable, str(_SCRIPT)], cwd=tmp_path, capture_output=True, text=True)
    assert bad.returncode == 1
    assert "bad.py:1  AWS access key id" in bad.stderr
    assert "QRSTUVWXYZ" not in bad.stderr + bad.stdout  # never echoes the secret
