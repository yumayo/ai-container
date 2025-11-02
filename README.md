# ビルド方法

```sh
docker build --no-cache -t yumayo-ai -f Dockerfile .
```

# 起動前の準備

```sh
mkdir -p .claude.local
[ ! -f .claude.local/.claude.json ] && echo '{}' > .claude.local/.claude.json || true
mkdir -p .claude.local/.claude
```

# 起動方法

```sh
docker run -ti --rm \
--stop-timeout 0 \
--cap-add=NET_ADMIN \
--cap-add=NET_RAW \
--mount type=bind,source="$(pwd)",target=/workspace \
--mount type=tmpfs,target=/workspace/.claude.local,tmpfs-size=0 \
--mount type=bind,source="$(pwd)/.claude.local/.claude",target=/home/ubuntu/.claude \
--mount type=bind,source="$(pwd)/.claude.local/.claude.json",target=/home/ubuntu/.claude.json \
yumayo-ai \
bash -ci "claude --dangerously-skip-permissions"
```

# よくある使い方

~/.bash_ai_container に下記を貼り付けます。

```sh
#!/bin/bash

aicontainer() {
  mkdir -p .claude.local
  [ ! -f .claude.local/.claude.json ] && echo '{}' > .claude.local/.claude.json || true
  mkdir -p .claude.local/.claude

  # .aiignoreの各行を読み込んでtmpfsマウントや/dev/nullにマウントすることで、機密情報へのアクセスをブロックします。
  AI_IGNORE=""
  if [ -f .aiignore ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      if [ -n "$line" ] && [[ ! "$line" =~ ^[[:space:]]*# ]]; then
        line="${line#/}"
        if [ -d "$line" ]; then
          AI_IGNORE="$AI_IGNORE --mount type=tmpfs,target=/workspace/$line,tmpfs-size=0"
        elif [ -f "$line" ]; then
          AI_IGNORE="$AI_IGNORE --mount type=bind,source=/dev/null,target=/workspace/$line,readonly"
        fi
        echo ignored. $line
      fi
    done < .aiignore
  fi

  docker run -ti --rm \
    --stop-timeout 0 \
    --cap-add=NET_ADMIN \
    --cap-add=NET_RAW \
    --mount type=bind,source="$(pwd)",target=/workspace \
    --mount type=tmpfs,target=/workspace/.claude.local,tmpfs-size=0 \
    --mount type=bind,source="$(pwd)/.claude.local/.claude",target=/home/ubuntu/.claude \
    --mount type=bind,source="$(pwd)/.claude.local/.claude.json",target=/home/ubuntu/.claude.json \
    $AI_IGNORE \
    yumayo-ai \
    bash -ci "claude --dangerously-skip-permissions"
}
```

~/.bashrc に下記のプログラムを追記します。

```sh
if [ -f ~/.bash_ai_container ]; then
    . ~/.bash_ai_container
fi
```

以降は、aicontainerとコマンドを実行することで、任意の場所でaiコンテナを起動することができます。
