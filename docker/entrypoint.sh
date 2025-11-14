#!/bin/bash
set -e

# Run firewall initialization
echo "Initializing firewall..."
sudo /usr/local/bin/init-firewall.sh

# Execute the command passed to the container
exec "$@"
