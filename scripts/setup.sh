#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py setup_roles
.venv/bin/python manage.py setup_production
.venv/bin/python manage.py setup_component_routes
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py check
