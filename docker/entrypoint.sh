#!/bin/bash
set -e

# Run firewall initialization with mode (default: claude)
FIREWALL_MODE="${FIREWALL_MODE:-claude}"
echo "Initializing firewall (mode: $FIREWALL_MODE)..."
sudo /usr/local/bin/init-firewall.sh "$FIREWALL_MODE"

# Execute the command passed to the container
exec "$@"
