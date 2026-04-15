#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'
BOLD='\033[1m'

log_now() {
    date '+%H:%M:%S'
}

log_step() {
    echo -e "${BLUE}${BOLD}[$(log_now)] ▶ $1${NC}"
}

log_success() {
    echo -e "${GREEN}[$(log_now)] ✓ $1${NC}"
}

log_error() {
    echo -e "${RED}[$(log_now)] ✗ $1${NC}" >&2
}

# Run firewall initialization with mode (default: claude)
FIREWALL_MODE="${FIREWALL_MODE:-claude}"
log_step "Initializing firewall (mode: $FIREWALL_MODE)..."
if sudo /usr/local/bin/init-firewall.sh "$FIREWALL_MODE"; then
    log_success "Firewall initialization complete"
else
    log_error "Firewall initialization failed"
    exit 1
fi

# Execute the command passed to the container
exec "$@"
