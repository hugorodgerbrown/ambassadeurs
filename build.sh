#!/usr/bin/env bash
# Render build step for the single web service.
#
# Requires uv and Node on PATH in the build environment. Runs on every deploy;
# migrations run here so the DB is current before the new release serves
# traffic. The Render runtime specifics (uv install, Node availability, plan
# names) are finalised in the deployment ticket.
set -o errexit

pip install uv
uv sync --frozen --no-dev
npm ci
npm run css:build
uv run python manage.py collectstatic --no-input
uv run python manage.py migrate
