#!/bin/bash
# Called by wsl-containers.ps1 inside the trusted relay container.
# Load the base image's Node environment, then replace Bash with the relay CLI.
set -e
export NVM_DIR=/home/ubuntu/.nvm
source "$NVM_DIR/nvm.sh"
exec node "$(dirname -- "${BASH_SOURCE[0]}")/docker-socket-relay.cjs" "$@"
