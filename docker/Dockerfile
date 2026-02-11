FROM ubuntu:24.04

RUN apt-get update -y
RUN apt-get upgrade -y

RUN apt-get install -y sudo vim locales curl wget iputils-ping iptables ipset dnsutils jq iproute2 git ffmpeg

# dockerコンテナ内で日本語が入力できないため設定します。
RUN locale-gen ja_JP.UTF-8
ENV TERM=xterm-256color
ENV LANG=ja_JP.UTF-8
ENV TZ=Asia/Tokyo

# ubuntuユーザーをsudoグループから明示的に削除してsudo実行を防止
RUN gpasswd -d ubuntu sudo

# ubuntuユーザーのホームディレクトリに.hushloginファイルを作成してMOTDを抑制
# .hushloginファイルが無いと下記のメッセージが表示されます。
# To run a command as administrator (user "root"), use "sudo <command>".
# See "man sudo_root" for details.
RUN touch /home/ubuntu/.hushlogin
RUN chown ubuntu:ubuntu /home/ubuntu/.hushlogin

USER ubuntu

RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash

RUN bash -c "source $HOME/.nvm/nvm.sh && \
nvm install 24 && \
nvm use 24 && \
nvm alias default 24 && \
npm install -g npm \
"

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

WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
