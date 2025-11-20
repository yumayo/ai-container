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

# Step 1: Dockerネットワークの作成
print_step "Step 1/2: Creating Docker network 'yumayo-ai'..."
if docker network inspect yumayo-ai &>/dev/null; then
    print_warning "Network 'yumayo-ai' already exists. Skipping..."
else
    if docker network create yumayo-ai &>/dev/null; then
        print_success "Network 'yumayo-ai' created successfully"
    else
        print_error "Failed to create network 'yumayo-ai'"
        exit 1
    fi
fi

echo ""

# Step 2: Dockerイメージのビルド
print_step "Step 2/2: Building Docker image 'yumayo-ai'..."
print_info "This may take a few minutes..."

if (cd docker && docker build --no-cache -t yumayo-ai -f Dockerfile .); then
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
