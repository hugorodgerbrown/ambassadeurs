#!/usr/bin/env bash
# Render build step for the single web service.
#
# Requires uv and Node on PATH in the build environment. Runs on every deploy;
# migrations run here so the DB is current before the new release serves
# traffic. The Render runtime specifics (uv install, Node availability, plan
# names) are finalised in the deployment ticket.
#
# GeoLite2-City download: if MAXMIND_ACCOUNT_ID and MAXMIND_LICENSE_KEY are both
# set, the MaxMind GeoLite2 City database is downloaded and extracted to
# geoip/GeoLite2-City.mmdb before static files are collected. MaxMind's download
# API authenticates with HTTP Basic auth as ACCOUNT_ID:LICENSE_KEY — both are
# required. If either is absent, this step is skipped and geolocation gracefully
# stores empty strings. Safe to run from either service (web or headless cron).
set -o errexit

pip install uv
uv sync --frozen --no-dev
npm ci
npm run css:build

# --- Download MaxMind GeoLite2-City database (guarded) ----------------------
if [ -n "${MAXMIND_ACCOUNT_ID:-}" ] && [ -n "${MAXMIND_LICENSE_KEY:-}" ]; then
    echo "Downloading MaxMind GeoLite2-City database..."
    GEOIP_DIR="geoip"
    GEOIP_DEST="${GEOIP_DIR}/GeoLite2-City.mmdb"
    GEOIP_ARCHIVE="${GEOIP_DIR}/GeoLite2-City.tar.gz"
    mkdir -p "${GEOIP_DIR}"

    MAXMIND_URL="https://download.maxmind.com/geoip/databases/GeoLite2-City/download?suffix=tar.gz"
    HTTP_STATUS=$(
        curl --location --silent --show-error \
            --user "${MAXMIND_ACCOUNT_ID}:${MAXMIND_LICENSE_KEY}" \
            --output "${GEOIP_ARCHIVE}" \
            --write-out "%{http_code}" \
            "${MAXMIND_URL}"
    )

    if [ "${HTTP_STATUS}" != "200" ]; then
        echo "MaxMind download returned HTTP ${HTTP_STATUS}." >&2
        if [ -f "${GEOIP_DEST}" ]; then
            echo "Stale database exists; continuing with it." >&2
        else
            echo "No existing database; geolocation will be disabled." >&2
            rm -f "${GEOIP_ARCHIVE}"
        fi
    else
        # Validate the archive is real gzip before extracting.
        if ! gzip --test "${GEOIP_ARCHIVE}" 2>/dev/null; then
            echo "Downloaded archive failed gzip validation (possible auth error page)." >&2
            rm -f "${GEOIP_ARCHIVE}"
            if [ -f "${GEOIP_DEST}" ]; then
                echo "Stale database exists; continuing with it." >&2
            fi
        else
            tar -xzf "${GEOIP_ARCHIVE}" -C "${GEOIP_DIR}" --strip-components=1 \
                --wildcards '*.mmdb'
            # Rename the extracted file to the expected path if needed.
            extracted=$(find "${GEOIP_DIR}" -maxdepth 1 -name '*.mmdb' | head -1)
            if [ "${extracted}" != "${GEOIP_DEST}" ] && [ -f "${extracted}" ]; then
                mv "${extracted}" "${GEOIP_DEST}"
            fi
            rm -f "${GEOIP_ARCHIVE}"
            echo "GeoLite2-City database installed at ${GEOIP_DEST}."
        fi
    fi
else
    echo "MAXMIND_ACCOUNT_ID / MAXMIND_LICENSE_KEY not set; skipping GeoLite2 download. Geolocation disabled."
fi
# ---------------------------------------------------------------------------

uv run python manage.py collectstatic --no-input
uv run python manage.py migrate
