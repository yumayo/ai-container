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

TARGET="${1:-all}"
NO_CACHE_OPT=""
if [ "$TARGET" = "rebuild" ]; then
    TARGET="all"
    NO_CACHE_OPT="--no-cache"
fi

build_base() {
    print_step "Building base image 'yumayo-ai-base'..."
    if (cd docker/aicontainer && docker build $NO_CACHE_OPT -t yumayo-ai-base -f Dockerfile.base .); then
        print_success "Base image 'yumayo-ai-base' built successfully"
    else
        echo ""
        print_error "Failed to build base image"
        exit 1
    fi
    echo ""
}

build_proxy() {
    print_step "Building proxy image 'yumayo-ai-proxy'..."
    if (cd docker/docker-proxy && docker build --no-cache -t yumayo-ai-proxy .); then
        print_success "Proxy image 'yumayo-ai-proxy' built successfully"
    else
        echo ""
        print_error "Failed to build proxy image"
        exit 1
    fi
    echo ""
}

build_main() {
    print_step "Building main image 'yumayo-ai'..."
    if (cd docker/aicontainer && docker build --no-cache -t yumayo-ai .); then
        echo ""
        print_success "Docker image 'yumayo-ai' built successfully"
    else
        echo ""
        print_error "Failed to build Docker image"
        exit 1
    fi
}

case "$TARGET" in
    all)
        build_base
        build_proxy
        build_main
        ;;
    base)
        build_base
        ;;
    proxy)
        build_proxy
        ;;
    main)
        build_main
        ;;
    *)
        print_error "Unknown target: $TARGET"
        print_info "Usage: bash install.sh [all|base|proxy|main|rebuild]"
        exit 1
        ;;
esac

# 完了メッセージ
echo ""
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}✓ Installation completed successfully!${NC}"
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
