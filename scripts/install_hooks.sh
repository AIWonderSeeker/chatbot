#!/bin/sh
# Use the repo's hooks (.githooks/) for this clone. Run once after cloning.
set -e
cd "$(git rev-parse --show-toplevel)"
chmod +x .githooks/* scripts/check_secrets.py
git config core.hooksPath .githooks
echo "Git hooks installed: pre-commit secret scan is active."
