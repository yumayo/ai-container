#!/bin/bash
set -e

# カラー定義
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# ヘッダー表示
print_header() {
    echo ""
    echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}${BOLD}  Yumayo AI - Uninstallation Script${NC}"
    echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

# ステップ表示
print_step() {
    echo -e "${BLUE}${BOLD}▶ $1${NC}"
}

# 成功表示
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

# エラー表示
print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# 警告表示
print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

# 情報表示
print_info() {
    echo -e "${MAGENTA}ℹ $1${NC}"
}

# ヘッダー表示
print_header

# Step 1: proxyコンテナの停止・削除、ボリューム・イメージの削除
print_step "Step 1/4: Cleaning up proxy resources..."

PROXY_NAME="yumayo-ai-proxy"
PROXY_VOLUME="yumayo-ai-proxy-sock"
PROXY_IMAGE="yumayo-ai-proxy"

if docker ps -aq -f name="^${PROXY_NAME}$" | grep -q .; then
    print_info "Stopping proxy container '$PROXY_NAME'..."
    docker stop "$PROXY_NAME" &>/dev/null || true
    docker rm "$PROXY_NAME" &>/dev/null || true
    print_success "Proxy container '$PROXY_NAME' removed"
else
    print_info "Proxy container '$PROXY_NAME' not found. Skipping..."
fi

if docker volume inspect "$PROXY_VOLUME" &>/dev/null; then
    docker volume rm "$PROXY_VOLUME" &>/dev/null || true
    print_success "Proxy volume '$PROXY_VOLUME' removed"
else
    print_info "Proxy volume '$PROXY_VOLUME' not found. Skipping..."
fi

if docker image inspect "$PROXY_IMAGE" &>/dev/null; then
    if docker rmi "$PROXY_IMAGE" &>/dev/null; then
        print_success "Proxy image '$PROXY_IMAGE' removed"
    else
        print_error "Failed to remove proxy image '$PROXY_IMAGE'"
    fi
else
    print_info "Proxy image '$PROXY_IMAGE' not found. Skipping..."
fi

echo ""

# Step 2: yumayo-ai プレフィックスのネットワークに接続されているコンテナの停止と削除
print_step "Step 2/4: Stopping and removing containers on 'yumayo-ai*' networks..."
NETWORKS=$(docker network ls --filter "name=^yumayo-ai" --format '{{.Name}}' 2>/dev/null || echo "")
if [ -n "$NETWORKS" ]; then
    for net in $NETWORKS; do
        CONTAINERS=$(docker network inspect "$net" -f '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null || echo "")
        if [ -n "$CONTAINERS" ]; then
            print_info "Network '$net': found containers: $CONTAINERS"
            for container in $CONTAINERS; do
                print_info "Stopping container '$container'..."
                docker stop "$container" &>/dev/null || true
                print_info "Removing container '$container'..."
                docker rm "$container" &>/dev/null || true
            done
        else
            print_info "Network '$net': no containers found"
        fi
    done
    print_success "All containers stopped and removed"
else
    print_info "No 'yumayo-ai*' networks found"
fi

echo ""

# Step 3: Dockerイメージの削除
print_step "Step 3/4: Removing Docker image 'yumayo-ai'..."
if docker image inspect yumayo-ai &>/dev/null; then
    if docker rmi yumayo-ai &>/dev/null; then
        print_success "Docker image 'yumayo-ai' removed successfully"
    else
        print_error "Failed to remove Docker image 'yumayo-ai'"
        print_warning "There might be containers using this image"
    fi
else
    print_warning "Docker image 'yumayo-ai' does not exist. Skipping..."
fi

echo ""

# Step 4: yumayo-ai プレフィックスのDockerネットワークをすべて削除
print_step "Step 4/4: Removing Docker networks 'yumayo-ai*'..."
NETWORKS=$(docker network ls --filter "name=^yumayo-ai" --format '{{.Name}}' 2>/dev/null || echo "")
if [ -n "$NETWORKS" ]; then
    for net in $NETWORKS; do
        if docker network rm "$net" &>/dev/null; then
            print_success "Network '$net' removed successfully"
        else
            print_error "Failed to remove network '$net'"
            print_warning "There might be containers still using this network"
        fi
    done
else
    print_info "No 'yumayo-ai*' networks found"
fi

# 完了メッセージ
echo ""
echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}✓ Uninstallation completed successfully!${NC}"
echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
