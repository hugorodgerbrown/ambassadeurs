#!/usr/bin/env bash
#
# Run the e2e suite locally the same way CI does. This is the "as close as
# possible" entry point: it uses the SAME container images (via compose.yaml as
# the CI service containers), the SAME settings module, and the SAME environment
# variables as .github/workflows/e2e.yml. The only difference is that here we
# start the containers ourselves; in CI the runner does.
#
# Usage:
#   e2e/run-local.sh                 # whole suite
#   e2e/run-local.sh 03-matching     # a subset (args are passed to playwright)
#
# Env vars below can be overridden; the defaults match the CI job exactly.
set -euo pipefail

cd "$(dirname "$0")/.." # repo root

export DJANGO_SETTINGS_MODULE=config.settings.e2e
export SECRET_KEY="${SECRET_KEY:-ci-e2e-secret-key}"
export DATABASE_URL="${DATABASE_URL:-postgres://ambassadeurs:ambassadeurs@127.0.0.1:5432/ambassadeurs_e2e}"
export EMAIL_HOST="${EMAIL_HOST:-127.0.0.1}"
export EMAIL_PORT="${EMAIL_PORT:-1025}"
export BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
export MAILPIT_URL="${MAILPIT_URL:-http://127.0.0.1:8025}"

echo "==> Starting backing services (postgres, mailpit) via compose"
docker compose -f e2e/compose.yaml up -d --wait

echo "==> Building Tailwind CSS"
npm run css:build --silent

echo "==> Applying migrations"
uv run python manage.py migrate --noinput

echo "==> Collecting static files"
uv run python manage.py collectstatic --noinput >/dev/null

echo "==> Running Playwright (boots Django via webServer)"
cd e2e
npx playwright test "$@"
