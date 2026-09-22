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
aicontainer bash         # .aicontainer の tool を維持して bash を起動
```

認証情報・チャット履歴はモードごとに `.claude.local` / `.codex.local` / `.claude.ollama` へ分離保存されます。

Codex CLI は WebSearch を無効化した状態（`web_search=disabled`）で起動します。

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
- ワークスペース直下、または `.aimount` した各マウント元の直下にある `.aiignore` のみ処理
- globパターン（`*.log` 等）は未サポート

### `.aicontainer` — コンテナ動作設定

```
network=myproject                      # Dockerネットワーク名（yumayo-ai-myproject）
session=../                            # セッション共有パス（複数プロジェクトで共有可能）
image=yumayo-ai-custom                 # 使用するDockerイメージ（デフォルト: yumayo-ai）
tool=claude                            # 既定ツール（claude / codex / claude-ollama）
path=./bin                             # コンテナ内PATHに追加（複数行指定可）
env=CLAUDE_CODE_EFFORT_LEVEL=max       # コンテナに環境変数を追加（複数行指定可）
before-start-up=./setup.sh             # コンテナ起動前にホスト側で実行するコマンド
docker-proxy-name=myproject            # Docker Socket Proxyを有効化（プロキシ名を指定）
docker-proxy-allow=npx playwright,node # execで許可するコマンド（カンマ区切り）
docker-proxy-containers=myapp,mydb     # execを許可するコンテナ名（カンマ区切り、未指定で全拒否）
```

`dns` には、追加で接続を許可するコンテナ名・ネットワークエイリアス・ドメイン名を1行に1つ指定できます。

```
network=myproject
dns=postgres
dns=redis
```

コンテナ起動時に、指定したDockerネットワークのDNSを使ってIPv4アドレスを解決し、`/etc/hosts` とIP許可リストに登録します。接続先のコンテナは先に起動してください。名前解決に失敗した場合はAIコンテナの起動を中止します。モード別のAnthropic／OpenAIのAPI・認証先は自動で登録されます。

同じDockerネットワークでも、`dns` に指定していない接続先のIPは許可しません。設定や接続先のIPを更新した場合はAIコンテナを再作成してください。

### `.aibin/` — コンテナ内コマンドを追加

`.aibin/` 直下の実行可能ファイルは、コンテナ起動時に PATH の先頭へ追加されます。
同名コマンドが他の PATH に存在する場合も `.aibin/` が最優先です。

```sh
mkdir -p .aibin
cat > .aibin/playwright <<'EOF'
#!/bin/sh
docker compose exec playwright npx playwright "$@"
EOF
chmod +x .aibin/playwright
```

AIコンテナ内では `playwright test` のように直接実行できます。

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
AI Container ──(Unix Socket)──> Go Proxy ──(Docker Socket)──> Docker Engine
  DOCKER_HOST=/var/run/            exec以外を                  /var/run/
  docker-proxy/docker.sock         403で拒否                   docker.sock
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

3. 必要なら `.aibin/playwright` を作成

```sh
mkdir -p .aibin
cat > .aibin/playwright <<'EOF'
#!/bin/sh
docker compose exec playwright npx playwright "$@"
EOF
chmod +x .aibin/playwright
```

4. `aicontainer` を起動し、AI内からコマンドを実行

```sh
playwright test
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

接続先は、モード別のAnthropic／OpenAIのAPI・認証先と、`.aicontainer` の `dns` に指定した名前から解決したIPv4アドレスに限定しています。登録済みIPへの送信とその応答は、DNS（UDP/TCP 53番）を除き全ポートで許可します。localhost、自分自身のIP、未登録のIP、IPv6通信は拒否します。

起動時に必要なドメインのIPv4アドレスを解決して `/etc/hosts` に登録し、その後はDNS通信（UDP/TCP 53番ポート）とDocker内蔵DNSへの接続を拒否します。IPアドレスを更新する場合はコンテナを再作成してください。

Dockerサブネット全体を許可するルールはありません。Dockerホストのゲートウェイや他コンテナも、IP許可リストに登録されていなければ拒否します。`dns` の設定は起動時のコピーを読み取り専用で渡すため、実行中にワークスペースの `.aicontainer` を編集しても許可先は増えません。

## アンインストール

```sh
bash uninstall.sh
```

## ライセンス

MIT
