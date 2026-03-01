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

再ビルドする場合:

```sh
bash install.sh rebuild  # ベースイメージ含め全て再ビルド
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

### `.aicontainer` — コンテナ動作設定

```
network=myproject                      # Dockerネットワーク名（yumayo-ai-myproject）
session=../                            # セッション共有パス（複数プロジェクトで共有可能）
docker-proxy-name=myproject            # Docker Socket Proxyを有効化（プロキシ名を指定）
docker-proxy-allow=npx playwright,node # execで許可するコマンド（カンマ区切り）
```

### `.aimount` — 追加マウント

```
./data:/workspace/data
$HOME/.gitconfig:/workspace/.gitconfig
${API_KEY?}:/workspace/api-key
```

環境変数展開（`$VAR`, `${VAR:-default}`, `${VAR?}` 等）に対応。

## Docker Socket Proxy（外部コンテナ連携）

Playwright などのツールをAIコンテナに入れず、ホスト上の別コンテナで実行して `docker compose exec` で呼び出せます。

```
AI Container ──(Unix Socket)──> nginx Proxy ──(Docker Socket)──> Docker Engine
  DOCKER_HOST=/var/run/             exec以外を                  /var/run/
  docker-proxy/docker.sock          403で拒否                   docker.sock
```

### 手順

1. ホスト側で外部コンテナを起動しておく

```sh
docker compose up -d
```

2. `.aicontainer` に設定を追加

```
docker-proxy-name=playwright
docker-proxy-allow=npx playwright
```

3. `aicontainer` を起動し、AI内からコマンドを実行

```sh
docker compose exec playwright npx playwright test
```

### セキュリティ

プロキシは2段階でアクセスを制限します。

**APIレベル**: `exec` 関連のエンドポイントのみ許可します。コンテナの作成・削除やイメージ操作は全て拒否されます。

```sh
docker run ubuntu echo hello  # 403 Forbidden
docker rm mycontainer         # 403 Forbidden
```

**コマンドレベル**: `docker-proxy-allow` で指定したコマンドのみ `docker exec` で実行できます。未指定の場合、全てのコマンドが拒否されます。コマンド名に続けて引数も指定でき、前方一致で判定します。

```sh
# docker-proxy-allow=npx playwright,node の場合
docker compose exec myapp npx playwright test  # OK（npx playwright に一致）
docker compose exec myapp npx webpack          # 403 Forbidden
docker compose exec myapp node script.js       # OK（node に一致）
docker compose exec myapp cat .env             # 403 Forbidden
docker compose exec myapp /usr/bin/npx playwright test  # OK（フルパスでも判定可能）
```

## セキュリティ

コンテナのネットワークはファイアウォールで制限されており、AIツールのAPIドメインのみ通信可能です。

## アンインストール

```sh
bash uninstall.sh
```

## ライセンス

MIT
