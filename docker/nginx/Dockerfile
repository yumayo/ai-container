FROM nginx:1.29.5

# nginxのworkerプロセスがdocker.sockにアクセスできるようにrootで実行
RUN sed -i 's/^user  nginx;/user  root;/' /etc/nginx/nginx.conf

COPY docker-proxy.conf /etc/nginx/conf.d/default.conf
COPY docker-proxy-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
