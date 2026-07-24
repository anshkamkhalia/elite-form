#!/usr/bin/env bash
set -euo pipefail

# run_app.sh

export FLASK_APP=app.py
export FLASK_ENV=development

python3 -m api.app