#!/bin/sh

# 許可コマンドリストを書き出し（未指定時は空ファイル＝全コマンド許可）
echo -n "${DOCKER_PROXY_ALLOW:-}" > /etc/nginx/docker-proxy-allow.txt

# 前回起動していた内容が残ることがあるため削除しておく。
rm -f /var/run/docker-proxy/docker.sock

nginx -g "daemon off;" &
NGINX_PID=$!
retries=0
while [ ! -S /var/run/docker-proxy/docker.sock ]; do
  sleep 0.1
  retries=$((retries + 1))
  if [ $retries -ge 100 ]; then
    echo "Error: docker-proxy socket not ready after 10s" >&2
    kill $NGINX_PID 2>/dev/null
    exit 1
  fi
done
chmod 666 /var/run/docker-proxy/docker.sock
wait $NGINX_PID
