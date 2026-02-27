#!/bin/bash
# Daily Newsletter Digest Runner
#
# Usage:
#   bash scheduler/run_digest.sh
#
# This script is intended to be invoked by launchd (macOS) or any cron-
# compatible scheduler.  It activates the project virtual environment,
# then delegates to the main CLI entry-point.

set -euo pipefail

PROJECT_DIR="/Users/hooeni/SideWorkspace/mailing_summary"

cd "$PROJECT_DIR"

# Activate virtual environment
source .venv/bin/activate

# Run the digest pipeline and append all output to the run log
python main.py run-digest >> logs/run.log 2>&1
