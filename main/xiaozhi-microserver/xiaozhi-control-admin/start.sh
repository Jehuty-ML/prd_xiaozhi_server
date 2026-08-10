#!/usr/bin/env bash
# Thin wrapper; prefer root start_dev_services.sh for full stack.
exec python "$(dirname "$0")/main.py" "$@"
