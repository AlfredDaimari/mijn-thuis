#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATABASE="$SCRIPT_DIR/data/test.sqlite3"

cd "$SCRIPT_DIR"
python3 -m pytest -m "not integration" -vv

if [[ ! -f "$DATABASE" ]]; then
  echo "Missing $DATABASE. Run the opt-in live-flow test once to seed it." >&2
  exit 1
fi

# Preserve the copied email records. Clear only derived queues, then requeue
# the captured emails through the database layer.
python3 -c '
import sys
from query_service.database import PipelineDatabase

database = PipelineDatabase(sys.argv[1])
database.clear_derived_queues_for_test_replay()
database.requeue_all_emails_for_test_replay()
' "$DATABASE"

python3 -m query_service.processor \
  --database "$DATABASE" \
  --once
