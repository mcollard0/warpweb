#!/usr/bin/env bash
# Keep installation logic testable and credentials out of shell arguments.
set -e
cd -- "$( dirname -- "${BASH_SOURCE[0]}" )"
exec python3 -m tools.install "$@"
