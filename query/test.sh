#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGING_DATABASE="$SCRIPT_DIR/data/test.sqlite3"
LISTINGS_DATABASE="$SCRIPT_DIR/data/test-listings.sqlite3"

cd "$SCRIPT_DIR"
python3 -m unittest discover -s tests -v

if [[ ! -f "$STAGING_DATABASE" ]]; then
  echo "Missing $STAGING_DATABASE. Run the opt-in live-flow test once to seed it." >&2
  exit 1
fi

# Preserve the copied email in the staging database. Delete only the separate
# test listings database from an earlier replay, then requeue the stored email.
rm -f "$LISTINGS_DATABASE"
sqlite3 "$STAGING_DATABASE" "UPDATE seen_emails SET is_read = 0, read_at = NULL, extraction_error = NULL;"

python3 -m query_service.processor \
  --staging-database "$STAGING_DATABASE" \
  --listings-database "$LISTINGS_DATABASE" \
  --once
