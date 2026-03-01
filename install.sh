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
    echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}${BOLD}  Yumayo AI - Installation Script${NC}"
    echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
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

# ベースイメージのビルド
print_step "Building base image 'yumayo-ai-base'..."

BASE_BUILD_OPTS=""
if [ "$1" = "rebuild-all" ]; then
    BASE_BUILD_OPTS="--no-cache"
    print_info "Rebuilding base image without cache..."
fi

if (cd docker/aicontainer && docker build $BASE_BUILD_OPTS -t yumayo-ai-base -f Dockerfile.base .); then
    print_success "Base image 'yumayo-ai-base' built successfully"
else
    echo ""
    print_error "Failed to build base image"
    exit 1
fi

echo ""

# プロキシイメージのビルド
print_step "Building proxy image 'yumayo-ai-proxy'..."

PROXY_BUILD_OPTS=""
if [ "$1" = "rebuild" ] || [ "$1" = "rebuild-all" ]; then
    PROXY_BUILD_OPTS="--no-cache"
    print_info "Rebuilding proxy image without cache..."
fi

if (cd docker/nginx && docker build $PROXY_BUILD_OPTS -t yumayo-ai-proxy .); then
    print_success "Proxy image 'yumayo-ai-proxy' built successfully"
else
    echo ""
    print_error "Failed to build proxy image"
    exit 1
fi

echo ""

# メインイメージのビルド
print_step "Building main image 'yumayo-ai'..."

BUILD_OPTS=""
if [ "$1" = "rebuild" ] || [ "$1" = "rebuild-all" ]; then
    BUILD_OPTS="--no-cache"
    print_info "Rebuilding without cache..."
fi

if (cd docker/aicontainer && docker build $BUILD_OPTS -t yumayo-ai .); then
    echo ""
    print_success "Docker image 'yumayo-ai' built successfully"
else
    echo ""
    print_error "Failed to build Docker image"
    exit 1
fi

# 完了メッセージ
echo ""
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}✓ Installation completed successfully!${NC}"
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
