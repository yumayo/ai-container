#!/bin/bash

# 参考: https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/.devcontainer/init-firewall.sh

set -euo pipefail  # コマンド失敗・未定義変数の参照・パイプラインの失敗時に終了する
IFS=$'\n\t'       # 単語の区切り文字を改行とタブに限定する

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
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

log_info() {
    echo -e "${MAGENTA}[$(log_now)] ℹ $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}[$(log_now)] ⚠ $1${NC}"
}

log_error() {
    echo -e "${RED}[$(log_now)] ✗ $1${NC}" >&2
}

# モード引数の処理（デフォルト: claude）
MODE="${1:-claude}"

case "$MODE" in
    claude)
        ALLOWED_DOMAINS=("api.anthropic.com")
        ;;
    codex)
        # ChatGPTサブスク利用時: chatgpt.com + 認証系
        # APIキー利用時: api.openai.com
        ALLOWED_DOMAINS=("api.openai.com" "chatgpt.com" "auth0.openai.com" "auth.openai.com")
        ;;
    *)
        log_error "Unknown mode '$MODE'. Use 'claude' or 'codex'"
        exit 1
        ;;
esac

# .aicontainerのdns設定は、ホスト側で作成した読み取り専用ファイルから取得する。
# 引数や環境変数からは追加せず、sudoで再実行しても許可先を増やせないようにする。
if [ -f /etc/aicontainer/dns ]; then
    while IFS= read -r domain || [ -n "$domain" ]; do
        domain="${domain,,}"
        if [[ ! "$domain" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]] || [ ${#domain} -gt 253 ]; then
            log_error "Invalid DNS name: $domain"
            exit 1
        fi
        for existing_domain in "${ALLOWED_DOMAINS[@]}"; do
            [ "$domain" = "$existing_domain" ] && continue 2
        done
        ALLOWED_DOMAINS+=("$domain")
    done < /etc/aicontainer/dns
fi

log_info "Firewall mode: $MODE"
log_info "Allowed domains: ${ALLOWED_DOMAINS[*]}"

# ファイアウォールを変更する前に、必要な名前をすべて解決する。
# getentは/etc/hostsも参照するため、DNSを無効化した後も再実行できる。
# 拒否確認用ドメインも解決し、疎通確認時にIP制限による拒否を検証できるようにする。
# これにより、DNSが使えないことによる失敗を、IP制限の成功と誤判定するのを防ぐ。
BLOCKED_DOMAIN="example.com"
declare -A DOMAIN_IPS
RESOLVED_DOMAINS=()
for domain in "${ALLOWED_DOMAINS[@]}" "$BLOCKED_DOMAIN"; do
    # 同じ名前が複数回指定されても、名前解決は1回だけ行う。
    [ -n "${DOMAIN_IPS[$domain]:-}" ] && continue
    log_step "Resolving $domain..."
    if ! ips=$(getent ahostsv4 "$domain" | awk '{print $1}' | sort -u) || [ -z "$ips" ]; then
        log_error "Failed to resolve $domain"
        exit 1
    fi

    while read -r ip; do
        if [[ ! "$ip" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
            log_error "Invalid IP from name resolution for $domain: $ip"
            exit 1
        fi
        IFS=. read -r -a octets <<< "$ip"
        for octet in "${octets[@]}"; do
            if (( 10#$octet > 255 )); then
                log_error "Invalid IP from name resolution for $domain: $ip"
                exit 1
            fi
        done
    done <<< "$ips"
    DOMAIN_IPS["$domain"]="$ips"
    RESOLVED_DOMAINS+=("$domain")
done

# このスクリプトが管理する行だけを更新し、Dockerや利用者が登録した行は保持する。
HOSTS_BEGIN="# BEGIN ai-container firewall"
HOSTS_END="# END ai-container firewall"
HOSTS_TEMP=$(mktemp)
trap 'rm -f "$HOSTS_TEMP"' EXIT
awk -v begin="$HOSTS_BEGIN" -v end="$HOSTS_END" '
    $0 == begin { managed = 1; next }
    $0 == end { managed = 0; next }
    !managed { print }
' /etc/hosts > "$HOSTS_TEMP"
printf '%s\n' "$HOSTS_BEGIN" >> "$HOSTS_TEMP"
for domain in "${RESOLVED_DOMAINS[@]}"; do
    while read -r ip; do
        printf '%s\t%s\n' "$ip" "$domain" >> "$HOSTS_TEMP"
    done <<< "${DOMAIN_IPS[$domain]}"
done
printf '%s\n' "$HOSTS_END" >> "$HOSTS_TEMP"
# /etc/hostsはDockerのバインドマウントなので、同じファイルに内容を上書きする。
cat "$HOSTS_TEMP" > /etc/hosts
log_success "Required names saved to /etc/hosts"

# ルールを初期化する前に、既定の動作を拒否に設定する。
# 許可するアドレスはIPv4のみとし、IPv6経由での迂回を防ぐ。
for firewall in iptables ip6tables; do
    "$firewall" -P INPUT DROP
    "$firewall" -P FORWARD DROP
    "$firewall" -P OUTPUT DROP
    "$firewall" -F
    "$firewall" -X
    "$firewall" -t nat -F
    "$firewall" -t nat -X
    "$firewall" -t mangle -F
    "$firewall" -t mangle -X
done
ipset destroy allowed-domains 2>/dev/null || true

# Docker内蔵DNSや自分自身のIPへの接続を含め、ループバック通信を拒否する。
# Docker内蔵DNSのNATルールも復元しない。
iptables -A INPUT -i lo -j DROP
iptables -A OUTPUT -o lo -j REJECT --reject-with icmp-admin-prohibited

# 許可済みの接続先も含め、DNSサーバーへの接続を拒否する。
for protocol in udp tcp; do
    iptables -A OUTPUT -p "$protocol" --dport 53 -j REJECT
done

# CIDR形式に対応したIPアドレスの許可リストを作成する。
ipset create allowed-domains hash:net

# /etc/hostsに保存した許可ドメインのIPを登録する。拒否確認用ドメインは含めない。
for domain in "${ALLOWED_DOMAINS[@]}"; do
    while read -r ip; do
        log_info "Adding $ip for $domain"
        ipset add allowed-domains "$ip" -exist
    done <<< "${DOMAIN_IPS[$domain]}"
done

# API・認証先とdns設定の名前から解決したIPへの通信を全ポートで許可する。
# ただし、上で設定したDNSの拒否を優先する。
# 応答の送信元IPも制限し、既存の接続が許可リストを迂回するのを防ぐ。
iptables -A INPUT -m set --match-set allowed-domains src -m state --state ESTABLISHED -j ACCEPT
iptables -A OUTPUT -m set --match-set allowed-domains dst -j ACCEPT

# その他の送信は明示的に拒否し、接続元へすぐにエラーを返す。
iptables -A OUTPUT -j REJECT --reject-with icmp-admin-prohibited
ip6tables -A OUTPUT -j REJECT --reject-with icmp6-adm-prohibited

log_success "Firewall configuration complete"
log_step "Verifying firewall rules..."

# 追加のdns設定と重複しないIPを選び、通信の拒否を確認する。
BLOCKED_IP=""
while read -r ip; do
    if ! ipset test allowed-domains "$ip" >/dev/null 2>&1; then
        BLOCKED_IP="$ip"
        break
    fi
done <<< "${DOMAIN_IPS[$BLOCKED_DOMAIN]}"
if [ -z "$BLOCKED_IP" ]; then
    log_warning "Skipping blocked-domain verification: all IPs for $BLOCKED_DOMAIN are allowed"
elif curl --noproxy '*' --connect-timeout 5 --resolve "$BLOCKED_DOMAIN:443:$BLOCKED_IP" "https://$BLOCKED_DOMAIN" >/dev/null 2>&1; then
    log_error "Firewall verification failed - was able to reach https://$BLOCKED_DOMAIN"
    exit 1
else
    log_success "Firewall verification passed - unable to reach https://$BLOCKED_DOMAIN as expected"
fi

# 許可されたドメインへのアクセスを確認
VERIFY_DOMAIN="${ALLOWED_DOMAINS[0]}"
if ! curl --noproxy '*' --connect-timeout 5 "https://$VERIFY_DOMAIN" >/dev/null 2>&1; then
    log_error "Firewall verification failed - unable to reach https://$VERIFY_DOMAIN"
    exit 1
else
    log_success "Firewall verification passed - able to reach https://$VERIFY_DOMAIN as expected"
fi
