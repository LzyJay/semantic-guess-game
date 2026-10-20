#!/usr/bin/env sh
# One-click launcher for macOS / Linux.
set -e
cd "$(dirname "$0")"
exec python3 server.py --serve waitress --threads 32
