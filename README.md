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
allow-ip=172.20.0.0/16                 # 通信を追加許可するIPv4アドレス/CIDR（複数行指定可）
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

`allow-ip` は、指定したIPv4アドレスまたはCIDR範囲との送受信を全ポートで許可します。
1行に1件指定でき、既存の許可ルール（APIドメイン、ゲートウェイを含む `/24` など）に追加されます。
未指定の場合は従来の動作です。IPv6やホスト名には対応していません。不正な値は起動時にエラーになります。

例えば、`yumayo-ai-myproject` のサブネットが `172.20.0.0/16` の場合:

```ini
network=myproject
allow-ip=172.20.0.0/16
allow-ip=192.168.1.10
```

実際のサブネットはホスト側で確認できます。上記のIP範囲は環境に合わせて変更してください。

```sh
docker network inspect yumayo-ai-myproject --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}'
```

この例では、同じネットワークに参加し、名前またはエイリアスが `mcp` のコンテナで
サーバーが `0.0.0.0:3000` で待ち受けていれば、AIコンテナから `http://mcp:3000` に接続できます。
`allow-ip` は通信の許可設定です。接続先への経路やDockerネットワークへの参加は別途必要です。
`aicontainer ollama`（`tool=claude-ollama`）はファイアウォール初期化を行わないため、この設定は適用されません。

この機能を既存環境に反映するには、イメージを再ビルドし、起動用関数を読み込み直してください。
`~/.bash_ai_container` にコピーして利用している場合は、そのファイルも更新してください。

```sh
bash install.sh main
source .bash_ai_container
```

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

通常のClaude Code / Codex CLIモードでは、ファイアウォールで通信を制限しています。
AIツールのAPIドメインの解決先IPv4、DNS（UDP 53）、SSH（TCP 22）、localhost、
デフォルトゲートウェイを含む `/24`、および `allow-ip` で追加したIPv4アドレス/CIDRとの通信を許可します。
`allow-ip` の許可範囲が広いほど、通信可能な接続先も増えます。
Ollamaモードは別ネットワークを使用し、このファイアウォール初期化を行いません。

## アンインストール

```sh
bash uninstall.sh
```

## ライセンス

MIT
