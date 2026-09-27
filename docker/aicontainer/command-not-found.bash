# 対話Bashは.bashrc、非対話BashはBASH_ENVから読み込む。
command_not_found_handle() {
    if [[ "${DOCKER_HOST:-}" == unix:///* && "$1" != */* ]] && command -v node >/dev/null 2>&1; then
        command node /usr/local/lib/aicontainer/docker-command-proxy.cjs "$@"
        return $?
    fi
    printf '%s: command not found\n' "$1" >&2
    return 127
}
