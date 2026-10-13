#!/bin/bash
# Trusted host-side launcher. This script is never the AI image's entrypoint.
set -e

repository=$1
workspace=$2
readonly wsl_run_id=$3
shift 3
[[ "$wsl_run_id" =~ ^[0-9a-f]{32}$ ]] || exit 1

# Label only the transient AI container and its dedicated public network.
# Persistent proxies and user-specified append-network networks remain intact.
docker() {
    if [[ "${1:-}" == run && "${2:-}" == -ti ]]; then
        command docker run --label "io.yumayo.aicontainer.wsl-run=$wsl_run_id" "${@:2}"
    elif [[ "${1:-}" == network && "${2:-}" == create && " $* " == *' io.yumayo.aicontainer.network=public '* ]]; then
        command docker network create --label "io.yumayo.aicontainer.wsl-run=$wsl_run_id" "${@:3}"
    else
        command docker "$@"
    fi
}

cd "$workspace"
source "$repository/.bash_ai_container"
aicontainer "$@"
