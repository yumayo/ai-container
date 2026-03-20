#!/bin/sh

# 許可コマンドリストを書き出し（未指定時は空ファイル＝全コマンド拒否）
printf '%s' "${DOCKER_PROXY_ALLOW:-}" > /etc/docker-proxy/allow.txt

# 許可コンテナリストを書き出し（未指定時は空ファイル＝全コンテナ許可）
printf '%s' "${DOCKER_PROXY_CONTAINERS:-}" > /etc/docker-proxy/containers.txt

# 前回起動していた内容が残ることがあるため削除しておく
rm -f /var/run/docker-proxy/docker.sock

# Docker socketのGIDを取得し、proxyユーザーにそのグループを付与
DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
DOCKER_GROUP=$(getent group "$DOCKER_GID" | cut -d: -f1)
if [ -z "$DOCKER_GROUP" ]; then
  addgroup -g "$DOCKER_GID" -S dockerhost
  DOCKER_GROUP=dockerhost
fi
addgroup proxy "$DOCKER_GROUP"

# proxyユーザーがlistenソケットを作成できるようにする
chown proxy:proxy /var/run/docker-proxy

exec su-exec proxy /usr/local/bin/docker-proxy
