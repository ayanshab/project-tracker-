#!/usr/bin/env bash
# Runs every backend test suite against a scratch database (default: test_db).
# Usage from the repo root:  ./backend/_tests/run_all.sh
set -e
export POSTGRES_NAME="${POSTGRES_NAME:-test_db}"
cd "$(dirname "$0")"
for t in users projects milestones budgets audit approvals forecast; do
  echo "== $t"
  python3 "test_$t.py" 2>&1 | grep -v "^INFO" | grep -E "^FAIL|PASSED" || { echo "!! $t failed"; exit 1; }
done
echo "All backend suites passed."
