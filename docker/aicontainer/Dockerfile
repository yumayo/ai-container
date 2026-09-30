FROM golang:1.26-alpine AS dp-builder
WORKDIR /build
COPY ./dp/ .
RUN go mod init aicontainer-dp && \
    CGO_ENABLED=0 go test ./... && \
    CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /dp .

FROM yumayo-ai-base

RUN curl -fsSL https://claude.ai/install.sh | bash
RUN echo 'export PATH="$HOME/.local/bin:$PATH"' >> $HOME/.bashrc

# Install OpenAI Codex CLI
RUN bash -c "source $HOME/.nvm/nvm.sh && npm install -g @openai/codex"

# Add Node.js PATH to .bashrc
RUN echo 'export PATH="$HOME/.nvm/versions/node/$(node --version)/bin:$PATH"' >> /home/ubuntu/.bashrc

# dpで明示的にDocker Socket Proxy経由のコマンド実行を行う。
COPY --from=dp-builder /dp /usr/local/bin/dp

# 共通指示を各CLIのシステム／開発者指示として渡す。セッションの設定は書き換えない。
COPY --chmod=0644 ./system-prompt.md /etc/aicontainer/system-prompt.md
COPY --chmod=0755 ./ai-cli /usr/local/lib/aicontainer/ai-cli
USER root
RUN mkdir -p /usr/local/lib/aicontainer/bin /opt/aicontainer/bin && \
    ln -s /home/ubuntu/.local/bin/claude /usr/local/lib/aicontainer/bin/claude && \
    bash -c 'export NVM_DIR=/home/ubuntu/.nvm; source "$NVM_DIR/nvm.sh" && \
      ln -s "$(command -v codex)" /usr/local/lib/aicontainer/bin/codex && \
      ln -s "$(command -v node)" /usr/local/bin/node' && \
    ln -s /usr/local/lib/aicontainer/ai-cli /opt/aicontainer/bin/claude && \
    ln -s /usr/local/lib/aicontainer/ai-cli /opt/aicontainer/bin/codex
USER ubuntu
ENV PATH="/opt/aicontainer/bin:${PATH}"
# nvmや~/.local/binのPATH設定後も、対話Bashでは必ずラッパーを選ぶ。
RUN echo 'export PATH="/opt/aicontainer/bin:$PATH"' >> /home/ubuntu/.bashrc

# Copy and set up firewall script
COPY ./init-firewall.sh /usr/local/bin/
USER root
RUN chmod +x /usr/local/bin/init-firewall.sh && \
  echo "ubuntu ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh" > /etc/sudoers.d/ubuntu-firewall && \
  chmod 0440 /etc/sudoers.d/ubuntu-firewall
USER ubuntu

# Copy and set up entrypoint
COPY ./entrypoint.sh /usr/local/bin/
USER root
RUN chmod +x /usr/local/bin/entrypoint.sh
USER ubuntu

# ボリュームマウントポイント（セッション共有用）
RUN mkdir -p /home/ubuntu/.ai-session

WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
