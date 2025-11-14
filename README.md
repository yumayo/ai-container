# インストール方法

```sh
bash install.sh
```

# 起動方法

```sh
source .bash_ai_container
aicontainer
```

# .claude.localについて

Claude Code ではプロジェクトで使用する.claudeディレクトリがありますが、ユーザーディレクトリにも.claudeディレクトリが作成されます。  
プロジェクトをまたいで.claudeディレクトリは共有したくないため、.claude.localを生成してそれをマウントすることで、完全にプロジェクトごとに異なる環境でAIコンテナを動作させることができます。

# おすすめ

.bash_ai_container を ~/.bash_ai_containerにコピーします。

~/.bashrc に下記のプログラムを追記します。

```sh
if [ -f ~/.bash_ai_container ]; then
    . ~/.bash_ai_container
fi
```

以降は、aicontainerとコマンドを実行することで、任意の場所でaiコンテナを起動することができます。
