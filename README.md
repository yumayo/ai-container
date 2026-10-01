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
aicontainer bash         # .aicontainer の tool を維持して bash を起動
```

認証情報・チャット履歴はモードごとに `.claude.local` / `.codex.local` へ分離保存されます。

Codex CLI は WebSearch を無効化した状態（`web_search=disabled`）で起動します。

## AIコンテナの共通指示

コンテナ共通の指示は [`docker/aicontainer/system-prompt.md`](docker/aicontainer/system-prompt.md) で管理し、イメージ内の `/etc/aicontainer/system-prompt.md` に配置します。外部コンテナのコマンドを `dp` で実行するルールをここに記載しています。

`claude` / `codex` の起動ラッパーが、ワークスペースの `AGENTS.md` / `CLAUDE.md` より前の指示レイヤーとして本文を渡します。

- Claude Code: `--append-system-prompt-file` で既定のシステムプロンプトに追加します。
- Codex CLI: `-c developer_instructions=...` で開発者指示として渡します。既定のモデル指示は維持します。この起動では、保存済み設定の `developer_instructions` よりコンテナ共通指示が優先されます。

プロジェクトの `AGENTS.md` / `CLAUDE.md` も通常どおり読み込まれます。ワークスペースやセッション用の `.claude.local` / `.codex.local` に共通指示を書き込む必要はありません。`aicontainer bash` 内から `claude` / `codex` を起動する場合もラッパーを経由します。

共通指示を変更するには上記ファイルを編集し、`bash install.sh main` でイメージを再ビルドして新しいセッションを開始してください。独自ファイルを使う場合は、既存の `.aimount` で `/etc/aicontainer/system-prompt.md` にマウントすることもできます。

`共通システムプロンプトを読み込めません` と表示された場合は、ホスト側で `bash install.sh main` を実行し、AIコンテナを起動し直してください。イメージ内では `/etc/aicontainer` を `0755`、`system-prompt.md` を `0644` で配置し、`ubuntu` ユーザーで読み取れることをビルド時に検証します。`.aimount` で上書きしている場合はマウント元の権限が優先されるため、エラーに表示されるファイルと親ディレクトリの権限も確認してください。

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
network=myproject                      # 参加するDockerネットワーク名（未指定時は専用ネットワーク）
allow-dns=mcp-server                   # 追加で通信を許可するコンテナ名・ドメイン名（複数行指定可）
session=../                            # セッション共有パス（複数プロジェクトで共有可能）
image=yumayo-ai-custom                 # 使用するDockerイメージ（デフォルト: yumayo-ai）
tool=claude                            # 既定ツール（claude / codex）
path=./bin                             # コンテナ内PATHに追加（複数行指定可）
env=CLAUDE_CODE_EFFORT_LEVEL=max       # コンテナに環境変数を追加（複数行指定可）
before-start-up=./setup.sh             # コンテナ起動前にホスト側で実行するコマンド
docker-proxy-name=myproject            # Docker Socket Proxyを有効化（プロキシ名を指定）
docker-proxy-allow=npx playwright*     # execで許可するコマンド（1行1ルール、複数行指定可）
docker-proxy-allow=node
docker-proxy-containers=myapp         # execを許可するコンテナ名（複数行指定可、記載順で転送）
docker-proxy-containers=mydb
```

`network` 未指定時は、起動ごとに `yumayo-ai-<ランダム値>` という専用のbridgeネットワークを作成します。専用ネットワークではコンテナ間通信を無効化し、AIコンテナ終了時にネットワークも削除します。

`network` を指定した場合は、その名前のDockerネットワークに参加します。存在しなければbridgeネットワークを作成し、終了後も残します。MCPサーバーなどとネットワークを共有する場合に使用してください。共有ネットワークでも、通信先はAPI・認証先と `allow-dns` に指定した名前から解決したIPだけに制限します。

`aicontainer dump` の出力にも同じネットワーク準備・後始末を含みます。Ollamaモード（`aicontainer ollama` / `tool=claude-ollama`）は廃止済みで、指定すると起動前にエラーになります。

`docker-proxy-allow` と `docker-proxy-containers` は同じキーを複数行書いて指定します。未指定時はそれぞれ全コマンド・全コンテナを拒否します。従来のカンマ区切りの設定は、1項目につき1行へ書き換えてください。空の定義は無視します。

旧設定名 `dns` は `allow-dns` に変更しました。既存の `.aicontainer` は設定名を書き換えてください。

`allow-dns` には、追加で接続を許可するコンテナ名・ネットワークエイリアス・外部ドメイン名を1行に1つ指定できます。コンテナ名やエイリアスを使う場合は、`network` に同じDockerネットワークを指定してください。

```
allow-dns=api.example.org
```

コンテナ起動時に、参加したネットワークのDNSを使ってIPv4アドレスを解決し、`/etc/hosts` とIP許可リストに登録します。接続先のコンテナは先に起動してください。名前解決に失敗した場合はAIコンテナの起動を中止します。モード別のAnthropic／OpenAIのAPI・認証先は自動で登録されます。

API・認証先と `allow-dns` に指定した接続先以外のIPは許可しません。設定や接続先のIPを更新した場合はAIコンテナを再作成してください。

### ローカルのMCPサーバーに接続する

HTTPで接続するMCPサーバーが `myproject` ネットワーク上で `mcp-server` という名前で動作する場合、`.aicontainer` に次を指定します。

```ini
network=myproject
allow-dns=mcp-server
```

AIツール側のMCP接続URLは、サーバーの設定に合わせて、例えば `http://mcp-server:3000/mcp` とします。MCPサーバーはコンテナ内で `0.0.0.0` に待ち受けさせてください。同じネットワーク経由で接続するため、ホストへのポート公開は不要です。`allow-dns` は接続許可の設定であり、AIツールへのMCP登録は別途必要です。

ネットワークはコンテナ間通信と外部APIへの通信が可能なものを使用してください。`network` の指定だけではMCPへの通信は許可されず、`allow-dns` に指定したサーバーのIPへの通信を許可します。許可はIP単位で、DNSを除く全ポートが対象です。

### `.aimount` — 追加マウント

```
./data:/workspace/data
$HOME/.gitconfig:/workspace/.gitconfig
${API_KEY?}:/workspace/api-key
```

環境変数展開（`$VAR`, `${VAR:-default}`, `${VAR?}` 等）に対応。

## Docker Socket Proxy（外部コンテナ連携）

AIコンテナ内で `dp COMMAND [ARG ...]` を実行すると、`docker-proxy-containers` に登録した起動中のコンテナへ Docker Socket Proxy 経由で転送します。ローカルに同名のコマンドが存在しても、`dp npx playwright --version` のように外部コンテナで実行できます。`dp` はGoの標準ライブラリだけで実装した静的リンクのバイナリで、実行時にNode.jsやDocker CLIは不要です。既存の `docker exec` / `docker compose exec` による呼び出しも利用できます。

`docker-command-proxy.cjs` とBashの `command_not_found_handle` による自動転送は廃止しました。従来の呼び出しには `dp` を付けてください。`dp` を付けないコマンドはAIコンテナ内で実行します。

`.aibin/` の専用サポートは廃止しました。PATHへの自動追加と起動時のスキャンは行いません。従来の転送ラッパーは、転送先の実際のコマンド名を使い、`docker-proxy-allow` と `docker-proxy-containers` で設定する方式へ移行してください。

```
AI Container ──(Unix Socket)──> Go Proxy ──(Docker Socket)──> Docker Engine
  DOCKER_HOST=/var/run/            未許可の操作を              /var/run/
  docker-proxy/docker.sock         403で拒否                   docker.sock
```

AIコンテナとプロキシは、共有volumeにマウントしたUnixソケットで通信します。同じDockerネットワークに所属する必要はありません。プロキシは `--network none` で起動し、ホストのDocker EngineにもUnixソケットで接続します。旧構成で起動中のプロキシは、次回の `aicontainer` 起動時にネットワークなしの構成へ作り直します。実行先の外部コンテナは、それぞれのプロジェクトネットワークに置いたまま利用できます。

### 手順

1. ホスト側で外部コンテナを起動しておく

```sh
docker compose up -d
```

2. `.aicontainer` に設定を追加（コンテナ名はDockerの実際の名前を指定）

```
docker-proxy-name=myproject
docker-proxy-allow=python
docker-proxy-allow=pytest
docker-proxy-allow=npx playwright
docker-proxy-containers=myapp
docker-proxy-containers=mytools
```

`docker ps` / `docker compose ps` の一覧には、`docker-proxy-containers` に未登録のコンテナも表示されます。Composeのプロジェクト指定など、通常の表示条件はそのまま適用されます。一覧・詳細の参照は登録不要ですが、未登録のコンテナでコマンドを実行すると403エラーになります。

```text
コンテナ "myapp" でのコマンド実行は許可されていません。.aicontainer に docker-proxy-containers=myapp を追加し、aicontainer を起動し直してください。
```

Composeのサービス名やIDで指定した場合も、設定には実際のコンテナ名を追加してください。`dp` の転送は登録済みのコンテナだけを対象にします。

3. `aicontainer` を起動し、AIコンテナ内から `dp` でコマンドを実行

```sh
dp npx playwright --version
dp python script.py
dp pytest tests/
# 非対話シェル（AIツールからの実行）でも有効
bash -c 'dp pytest tests/'
```

### `dp` の動作

- `dp` に渡したコマンドは、AIコンテナ内のPATHを検索せず外部コンテナで実行します。`dp` は通常の実行ファイルなので、Bash以外のシェルやプログラムの `spawn` 等からも使用できます。
- コンテナを登録順に試し、転送先のPATHに実行ファイルがない場合だけ次のコンテナへ進みます。実行後の終了コードが127でも再試行しません。
- 引数、標準入力、標準出力、標準エラー、終了コードを引き継ぎます。対話端末ではTTYも利用できます。
- 作業ディレクトリはAIコンテナの現在のディレクトリを指定します。転送先にも同じ絶対パスでプロジェクトをマウントしてください。`.aiignore` は転送先のマウントには適用されません。
- 許可されていないコマンドは終了コード126、PATHに見つからないコマンドや起動中の対象コンテナがない場合は127、プロキシ未設定・コンテナ未登録・接続や実行開始のエラーは125になります。
- `dp /path/to/command` や `dp ./command` も転送先で実行します。引数をシェル文字列に変換せず渡すため、パイプやリダイレクトは呼び出し元のシェルで処理されます。

`docker-proxy-allow=npx playwright` は実際のコマンド列 `npx playwright ...` を許可する設定です。許可ルールに `dp` は付けません。`dp npx playwright test` と呼び出してください。

更新後は `bash install.sh main` でAIイメージを再ビルドしてAIコンテナを起動し直してください。今回の変更に伴うベースイメージやプロキシイメージの再ビルドは不要です。

`dp` のソースは `docker/aicontainer/dp/` にあります。AIイメージのビルド時にGo用ステージでテスト・コンパイルし、`CGO_ENABLED=0` と `-trimpath -ldflags="-s -w"` で生成したバイナリだけを `/usr/local/bin/dp` にコピーします。Goのコンパイラやソースは実行用イメージに追加しません。

転送処理のテストはLinux上でGoを使い、Dockerなしで実行できます。Node.jsの結合テストは、一時ディレクトリにGo版 `dp` をビルドして実行します（テストの実行にはGoとNode.jsが必要です）。

```sh
cd docker/aicontainer/dp
go test main.go terminal_linux.go main_test.go terminal_linux_test.go
cd ../../..
node --test test/test_dp.cjs
```

共通指示の注入・起動設定のテスト:

```sh
python3 -m unittest discover -s test -p 'test_*.py'
```

### セキュリティ

プロキシは2段階でアクセスを制限します。

**APIレベル**: コンテナの一覧・詳細の参照と `exec` 関連のエンドポイントを許可します。コマンドの実行先は `docker-proxy-containers` に登録したコンテナに制限され、未指定の場合は全コンテナでの実行を拒否します。コンテナの作成・削除やイメージ操作は全て拒否されます。

```sh
docker run ubuntu echo hello  # 403 Forbidden
docker rm mycontainer         # 403 Forbidden
```

**コマンドレベル**: `docker-proxy-allow` で指定したコマンドのみ `docker exec` で実行できます。未指定の場合、全てのコマンドが拒否されます。コマンド名に続けて引数も指定でき、コマンド列の先頭から照合します。一致した後の追加引数は許可します。

```sh
# docker-proxy-allow=npx playwright と docker-proxy-allow=node を別々の行で指定した場合
docker compose exec myapp npx playwright test  # OK（npx playwright に一致）
docker compose exec myapp npx webpack          # 403 Forbidden
docker compose exec myapp node script.js       # OK（node に一致）
docker compose exec myapp cat .env             # 403 Forbidden
docker compose exec myapp /usr/bin/npx playwright test  # OK（フルパスでも判定可能）
```

コマンド名と引数には `*` を指定できます。`*` は0文字以上の任意の文字に一致し、引数の境界は越えません。`?` や `[]` はワイルドカードとして扱いません。

```ini
docker-proxy-allow=python*
docker-proxy-allow=npx playwright*
# python / python3 / python3.12、npx playwright / npx playwright@latest などを許可
```

```ini
docker-proxy-allow=*
# docker-proxy-containers に登録したコンテナで、全コマンドを許可
```

`python` だけなら引数の有無を問わず許可します。`python *` は少なくとも1つの引数が必要です。`*` を指定した場合も、コンテナの許可リストとAPI制限は適用されます。

Goプロキシのテストは `cd docker/docker-proxy && go test main.go main_test.go` で実行できます。プロキシイメージのビルド時にも自動で実行します。

## セキュリティ

AIコンテナは既定で専用ネットワークを使い、`network` を指定した場合だけ他コンテナとネットワークを共有します。接続先はどちらの場合もコンテナ内のファイアウォールで制限します。外部コンテナのコマンド実行はDocker Socket Proxyを経由し、HTTPのMCPサーバーには `allow-dns` で許可して直接接続します。

接続先は、モード別のAnthropic／OpenAIのAPI・認証先と、`.aicontainer` の `allow-dns` に指定した名前から解決したIPv4アドレスに限定しています。登録済みIPへの送信とその応答は、DNS（UDP/TCP 53番）を除き全ポートで許可します。localhost、自分自身のIP、未登録のIP、IPv6通信は拒否します。

起動時に必要なドメインのIPv4アドレスを解決して `/etc/hosts` に登録し、その後はDNS通信（UDP/TCP 53番ポート）とDocker内蔵DNSへの接続を拒否します。IPアドレスを更新する場合はコンテナを再作成してください。

Dockerサブネット全体を許可するルールはありません。Dockerホストのゲートウェイや他コンテナも、IP許可リストに登録されていなければ拒否します。`allow-dns` の設定は起動時のコピーを読み取り専用で渡すため、実行中にワークスペースの `.aicontainer` を編集しても許可先は増えません。

## アンインストール

```sh
bash uninstall.sh
```

## ライセンス

MIT
