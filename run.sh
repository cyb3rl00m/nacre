#!/usr/bin/env bash
# Run Nacre straight from the source folder, without installing.
# Note: without installing, the password prompt shows a generic pkexec message.
cd "$(dirname "$(readlink -f "$0")")"
exec /usr/bin/python3 -m nacre "$@"
