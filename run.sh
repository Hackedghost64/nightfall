#!/usr/bin/env bash
# 🌙 Nightfall — one entrypoint. examples:
#   ./run.sh up          daemon start     ./run.sh status
#   ./run.sh tui         terminal app     ./run.sh key create mykey
#   ./run.sh query "Solo Leveling"        ./run.sh query "Naruto" --anime 1x1
#   ./run.sh download "Solo Leveling" 1x1  ./run.sh serve --port 8399
cd "$(dirname "$0")"
exec python3 -m nightfall "$@"
