#!/bin/sh
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
