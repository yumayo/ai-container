FROM nginx:1.29.5

# njsモジュールをインストール
RUN apt-get update && apt-get install -y nginx-module-njs && rm -rf /var/lib/apt/lists/*

# njsモジュールをロード（nginx.confの先頭に追加）
RUN sed -i '1i load_module modules/ngx_http_js_module.so;' /etc/nginx/nginx.conf

# nginxのworkerプロセスがdocker.sockにアクセスできるようにrootで実行
RUN sed -i 's/^user  nginx;/user  root;/' /etc/nginx/nginx.conf

COPY docker-proxy.conf /etc/nginx/conf.d/default.conf
RUN mkdir -p /etc/nginx/njs
COPY docker-proxy.js /etc/nginx/njs/docker-proxy.js
COPY docker-proxy-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
