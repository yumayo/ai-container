FROM golang:1.26-alpine AS builder
WORKDIR /build
COPY main.go .
RUN go mod init docker-proxy && \
    CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o docker-proxy .

FROM alpine:3.23
RUN apk add --no-cache su-exec && \
    adduser -D -u 1000 proxy && \
    mkdir -p /etc/docker-proxy && \
    chown proxy:proxy /etc/docker-proxy
COPY --from=builder /build/docker-proxy /usr/local/bin/docker-proxy
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
