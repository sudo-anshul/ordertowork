#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
uv run ruff check backend migrations scripts
uv run pytest
npm --prefix frontend run format:check
npm --prefix frontend run build
