#!/usr/bin/env bash
# Leak gate: fail if the tracked tree contains credential-shaped strings,
# private IPs/hostnames, or local gateway ports. Synthetic test fixtures
# are allow-listed explicitly.
set -euo pipefail

PATTERNS='(PK[A-Z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{20,}|192\.168\.|100\.6[4-9]\.|100\.7[0-1]\.|70\.23\.158|jupitervesta|spark-22fa|:8006|:8008|:8510)'
ALLOW='test_secrets_settings.py|test_paper_trading.py|test_watchlist_scanner.py'

hits=$(git grep -nIE "$PATTERNS" -- . ":!*$ALLOW" 2>/dev/null || true)
if [ -n "$hits" ]; then
  echo "LEAK CHECK FAILED — private material found in the tracked tree:"
  echo "$hits"
  exit 1
fi
echo "Leak check clean."
