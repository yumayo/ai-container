# AI Container

> AIコーディングツール（Claude Code / Codex CLI）をDockerコンテナで安全に実行する環境

## セットアップ

```sh
bash install.sh
source .bash_ai_container
```

常用する場合は `~/.bashrc` に追加:

```sh
cp .bash_ai_container ~/.bash_ai_container
echo 'if [ -f ~/.bash_ai_container ]; then . ~/.bash_ai_container; fi' >> ~/.bashrc
```

## 使い方

```sh
aicontainer              # Claude Code
aicontainer codex        # Codex CLI
aicontainer ollama MODEL # Claude Code + Ollama
```

認証情報・チャット履歴はモードごとに `.claude.local` / `.codex.local` / `.claude.ollama` へ分離保存されます。

## 設定ファイル

プロジェクトルートに配置して動作をカスタマイズできます。いずれも任意で、なくても動作します。

### `.aiignore` — AIに見せたくないファイルを指定

```
secret.txt
.env
node_modules
```

- `.aiignore` のあるディレクトリからの相対パスで指定
- ファイルは空ファイルに、ディレクトリは空ディレクトリに見える
- サブディレクトリにも配置可能（再帰的に処理）
- globパターン（`*.log` 等）は未サポート

### `.aicontainer` — ネットワーク・セッション設定

```
network=myproject       # Dockerネットワーク名（yumayo-ai-myproject）
session=../             # セッション共有パス（複数プロジェクトで共有可能）
```

### `.aimount` — 追加マウント

```
./data:/workspace/data
$HOME/.gitconfig:/workspace/.gitconfig
${API_KEY?}:/workspace/api-key
```

環境変数展開（`$VAR`, `${VAR:-default}`, `${VAR?}` 等）に対応。

## セキュリティ

コンテナのネットワークはファイアウォールで制限されており、AIツールのAPIドメインのみ通信可能です。

## アンインストール

```sh
bash uninstall.sh
```

## ライセンス

MIT
