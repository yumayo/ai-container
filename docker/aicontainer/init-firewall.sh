#!/bin/bash

# https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/.devcontainer/init-firewall.sh

set -euo pipefail  # Exit on error, undefined vars, and pipeline failures
IFS=$'\n\t'       # Stricter word splitting

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

validate_ipv4_range() {
    local range="$1" part
    local octet='(0|[1-9][0-9]{0,2})'
    local -a parts
    [[ "$range" =~ ^$octet\.$octet\.$octet\.$octet(/(0|[1-9][0-9]?))?$ ]] || return 1
    IFS='./' read -r -a parts <<< "$range"
    for part in "${parts[@]:0:4}"; do
        ((part <= 255)) || return 1
    done
    if [[ "$range" == */* ]]; then
        ((${range##*/} <= 32)) || return 1
    fi
    return 0
}

ipv4_to_number() {
    local a b c d
    IFS=. read -r a b c d <<< "$1"
    echo "$(((a << 24) | (b << 16) | (c << 8) | d))"
}

is_allowed_ip() {
    local ip="$1" range prefix mask network address
    [[ "$ip" != */* ]] && validate_ipv4_range "$ip" || return 1
    address=$(ipv4_to_number "$ip")
    for range in "${ALLOWED_IPS[@]}"; do
        prefix=32
        [[ "$range" != */* ]] || prefix="${range##*/}"
        network=$(ipv4_to_number "${range%/*}")
        mask=$(((0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF))
        if (( (address & mask) == (network & mask) )); then
            return 0
        fi
    done
    return 1
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

# 全件をルール変更前に検証する。第2引数はentrypointが渡すカンマ区切りのIPv4/CIDR。
ALLOWED_IPS=()
if [ -n "${2:-}" ]; then
    if [[ ! "$2" =~ ^[0-9./]+(,[0-9./]+)*$ ]]; then
        log_error "Invalid allow-ip list: specify IPv4 addresses or CIDRs"
        exit 1
    fi
    IFS=, read -r -a ALLOWED_IPS <<< "$2"
    for range in "${ALLOWED_IPS[@]}"; do
        if ! validate_ipv4_range "$range"; then
            log_error "Invalid allow-ip '$range': expected an IPv4 address or CIDR with prefix 0-32"
            exit 1
        fi
    done
fi

log_info "Firewall mode: $MODE"
log_info "Allowed domains: ${ALLOWED_DOMAINS[*]}"

# 1. Extract Docker DNS info BEFORE any flushing
DOCKER_DNS_RULES=$(iptables-save -t nat | grep "127\.0\.0\.11" || true)

# Flush existing rules and delete existing ipsets
iptables -F
iptables -X
iptables -t nat -F
iptables -t nat -X
iptables -t mangle -F
iptables -t mangle -X
ipset destroy allowed-domains 2>/dev/null || true

# 2. Selectively restore ONLY internal Docker DNS resolution
if [ -n "$DOCKER_DNS_RULES" ]; then
    log_step "Restoring Docker DNS rules..."
    iptables -t nat -N DOCKER_OUTPUT 2>/dev/null || true
    iptables -t nat -N DOCKER_POSTROUTING 2>/dev/null || true
    echo "$DOCKER_DNS_RULES" | xargs -L 1 iptables -t nat
else
    log_warning "No Docker DNS rules to restore"
fi

# First allow DNS and localhost before any restrictions
# Allow outbound DNS
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
# Allow inbound DNS responses
iptables -A INPUT -p udp --sport 53 -j ACCEPT
# Allow outbound SSH
iptables -A OUTPUT -p tcp --dport 22 -j ACCEPT
# Allow inbound SSH responses
iptables -A INPUT -p tcp --sport 22 -m state --state ESTABLISHED -j ACCEPT
# Allow localhost
iptables -A INPUT -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT

# Create ipset with CIDR support
ipset create allowed-domains hash:net

# Resolve and add allowed domains
for domain in "${ALLOWED_DOMAINS[@]}"; do
    log_step "Resolving $domain..."
    ips=$(dig +noall +answer A "$domain" | awk '$4 == "A" {print $5}')
    if [ -z "$ips" ]; then
        log_error "Failed to resolve $domain"
        exit 1
    fi

    while read -r ip; do
        if [[ ! "$ip" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
            log_error "Invalid IP from DNS for $domain: $ip"
            exit 1
        fi
        log_info "Adding $ip for $domain"
        ipset add allowed-domains "$ip"
    done < <(echo "$ips")
done

# Get host IP from default route
HOST_IP=$(ip route | grep default | cut -d" " -f3)
if [ -z "$HOST_IP" ]; then
    log_error "Failed to detect host IP"
    exit 1
fi

HOST_NETWORK=$(echo "$HOST_IP" | sed "s/\.[0-9]*$/.0\/24/")
log_info "Host network detected as: $HOST_NETWORK"

# Set up remaining iptables rules
iptables -A INPUT -s "$HOST_NETWORK" -j ACCEPT
iptables -A OUTPUT -d "$HOST_NETWORK" -j ACCEPT

for range in "${ALLOWED_IPS[@]}"; do
    log_info "Allowing traffic to/from $range"
    iptables -A INPUT -s "$range" -j ACCEPT
    iptables -A OUTPUT -d "$range" -j ACCEPT
done

# Set default policies to DROP first
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT DROP

# First allow established connections for already approved traffic
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Then allow only specific outbound traffic to allowed domains
iptables -A OUTPUT -m set --match-set allowed-domains dst -j ACCEPT

# Explicitly REJECT all other outbound traffic for immediate feedback
iptables -A OUTPUT -j REJECT --reject-with icmp-admin-prohibited

log_success "Firewall configuration complete"
log_step "Verifying firewall rules..."

# 明示的に許可した範囲に検証先が含まれる場合は、接続成功をエラーにしない。
if VERIFY_IP=$(curl -4 --connect-timeout 5 -o /dev/null -w '%{remote_ip}' https://example.com 2>/dev/null); then
    if is_allowed_ip "$VERIFY_IP"; then
        log_info "Blocked-access verification skipped: https://example.com ($VERIFY_IP) is in an allowed IP range"
    else
        log_error "Firewall verification failed - was able to reach https://example.com"
        exit 1
    fi
else
    log_success "Firewall verification passed - unable to reach https://example.com as expected"
fi

# 許可されたドメインへのアクセスを確認
VERIFY_DOMAIN="${ALLOWED_DOMAINS[0]}"
if ! curl --connect-timeout 5 "https://$VERIFY_DOMAIN" >/dev/null 2>&1; then
    log_error "Firewall verification failed - unable to reach https://$VERIFY_DOMAIN"
    exit 1
else
    log_success "Firewall verification passed - able to reach https://$VERIFY_DOMAIN as expected"
fi
