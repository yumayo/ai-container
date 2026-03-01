FROM yumayo-ai-base

RUN curl -fsSL https://claude.ai/install.sh | bash
RUN echo 'export PATH="$HOME/.local/bin:$PATH"' >> $HOME/.bashrc

# Install OpenAI Codex CLI
RUN bash -c "source $HOME/.nvm/nvm.sh && npm install -g @openai/codex"

# Add Node.js PATH to .bashrc
RUN echo 'export PATH="$HOME/.nvm/versions/node/$(node --version)/bin:$PATH"' >> /home/ubuntu/.bashrc

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
