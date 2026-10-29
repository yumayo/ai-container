# AI Container

> AIコーディングツール（Claude Code / Codex CLI）をDockerコンテナで安全に実行する環境

## セットアップ

Docker を利用できる Linux / WSL の Bash から実行します。

```sh
bash install.sh
source .bash_ai_container
```

常用する場合は `~/.bashrc` に追加:

```sh
cp .bash_ai_container ~/.bash_ai_container
echo 'if [ -f ~/.bash_ai_container ]; then . ~/.bash_ai_container; fi' >> ~/.bashrc
```

PoppyのWSLターミナルでは、Poppyが用意した`AICONTAINER_EVENT_SOCKET`を`aicontainer`が自動検出し、ソケットだけをコンテナへ渡します。対応するPoppy hookを導入すれば、`.aicontainer`への通知用URL・セッションID・IP許可の設定は不要です。通常のWSL内Dockerで動作します。ホスト側の`.bash_ai_container`を更新して読み込み直してください。この機能だけならイメージの再ビルドは不要です。

再ビルドする場合:

```sh
bash install.sh rebuild  # ベースイメージ含め全て再ビルド
```

Git LFS はベースイメージにインストールし、システム全体で初期設定済みです。既存環境への追加は、ホスト側で次を実行してからAIコンテナを起動し直してください。

```sh
bash install.sh base
bash install.sh main
```

## 使い方

```sh
aicontainer              # Claude Code
aicontainer codex        # Codex CLI
aicontainer bash         # .aicontainer の tool を維持して bash を起動
```

認証情報・チャット履歴はモードごとに `.claude.local` / `.codex.local` へ分離保存されます。

アプリから起動時だけ MCP などの設定を渡す場合は、JSON ファイルを `AICONTAINER_LAUNCH_PROFILE`、本リポジトリの `docker/aicontainer/launch-profile.cjs` を `AICONTAINER_LAUNCH_PROFILE_RUNNER` に指定します。プロジェクトのディレクトリで次を実行してください。既存の `.aicontainer` や AI ツールの設定ファイルは変更しません。

```sh
AICONTAINER_LAUNCH_PROFILE="$PWD/.runtime/launch.json" \
AICONTAINER_LAUNCH_PROFILE_RUNNER="/path/to/ai-container/docker/aicontainer/launch-profile.cjs" \
aicontainer codex
```

```json
{
  "arguments": ["-c", "mcp_servers.example={url=\"http://192.168.1.10:3000/\",bearer_token_env_var=\"EXAMPLE_TOKEN\"}"],
  "environment": { "EXAMPLE_TOKEN": "application-generated-secret" }
}
```

プロファイルと起動補助スクリプトには既存ファイルの絶対パスを指定し、`aicontainer claude` または `aicontainer codex` で起動してください。両ファイルを読み取り専用で渡し、コンテナ内で引数の配列と環境変数へ変換します。ファイル内容をシェルで実行したり、Docker の引数に出力したりしません。起動ログでは設定値・プロンプトを伏せ、環境変数の値は出力しません。共通システムプロンプトと既存の認証・履歴は維持します。現在のイメージが共通 CLI ラッパーに対応していれば、イメージの再ビルドは不要です。

形式は上記 2 プロパティのみで、ファイルは 1 MiB 以下、引数・環境変数は各 1,000 個以下です。値は NUL を含まない文字列、環境変数名は英字または `_` で始まる英数字・`_` に限ります。プロファイルと起動補助のパスに含まれるカンマ・空白・引用符も保持します。秘密を含むファイルは呼び出し元で Git の対象外にし、AI の終了後に削除してください。プロファイルで通信許可は増えません。HTTP MCP の接続先は `.aicontainer` の `allow-ip` / `allow-dns` で起動前に許可します。

Codex CLI の機能制限は、`.aicontainer` の `codex_mode` で切り替えられます。

| `codex_mode` | 起動時の設定 |
| --- | --- |
| `minimal`（既定） | 現在の最小構成。以下の許可リスト以外の feature を無効化します。 |
| `restricted` | 許可リスト導入前のコミット `e7e0d15` と同じ構成。標準 Web 検索・ページ取得と、内蔵 MCP の `apps`・`worktrees` だけを無効化します。 |

例えば、比較用の構成で起動する場合は次のように指定します。`minimal` に戻すと現在の最小構成になります。未指定時は `minimal`、それ以外の値や空の指定はエラーです。

```ini
codex_mode=restricted
```

`restricted` は `web_search=disabled`、`features.apps=false`、`features.worktrees=false` を渡します。`codex_apps` 経由の Web 検索や外部サービス連携と `codex_worktrees` 全体が無効になります。その他の feature は CLI の既定値・ユーザー設定を維持し、起動前の `features list` は実行しません。

`minimal` は、起動のたびに実体の CLI で `features list` を取得し、次の機能だけを有効にします。それ以外は、現在の有効・無効にかかわらず `--disable` を渡します。将来追加された機能も一覧に載れば自動的に無効化されます。一覧の取得・解析に失敗した場合や空だった場合は起動を中止します。

- `shell_tool`、`unified_exec`、`unified_exec_tty`: ローカルコマンドの実行・入出力
- `view_image`: ローカル画像の表示
- `hooks`: 既存のローカルフック
- `code_mode_host`: ローカルツールを呼び出す Code Mode の実行基盤
- `fast_mode`: `/fast` による Fast mode の切り替え。この許可だけでは Fast mode を自動選択しません
- `sleep_tool`: バックグラウンド処理の待ち合わせなどに使うローカルの待機ツール

`codex_apps`、`codex_worktrees`、画像生成、ブラウザー操作、プラグインなどの機能は、この許可リストの対象外です。廃止済み・非推奨の互換フラグは指定対象から除きます。廃止済みのフラグは CLI が `true` と表示する場合があります。どちらのモードでも、検索・ページ取得を行う標準 Web ツールは `web_search=disabled` で無効化します。Codex では WebFetch 相当のページ取得も標準 Web ツールが提供するため、個別の WebFetch 設定はありません。

モードは起動時に環境変数 `AICONTAINER_CODEX_MODE` で共通の起動ラッパーに渡すため、`aicontainer bash` 内からの直接起動や `codex exec` / `codex resume` にも適用されます。認証情報・履歴・ユーザー設定は両モードで共有します。比較する際は、同じモデル・同じ依頼で新しい会話を開始してください。

初回の反映には、ホスト側で `.bash_ai_container` を更新して `source .bash_ai_container` で読み込み直し（コピーして利用している場合はコピー先も更新）、`bash install.sh main` を実行して AIコンテナを起動し直してください。その後のモード切り替えは `.aicontainer` を変更し、AIコンテナを起動し直すだけで反映されます。

共通ラッパーは、実行直前のコマンドを `aicontainer: exec:` として標準エラーに表示します。実行ファイル、`--enable` / `--disable`、Web 検索設定などの自動追加した引数を、シェル用に引用して記録します。共通指示の本文と、呼び出し元が渡した設定値・プロンプト・未知の引数は `<redacted>` に置き換え、環境変数の値は出力しません。実際に渡す引数と標準出力は変更しないため、`codex exec --json` などにも利用できます。Claude の起動も同じ形式で記録します。

共通指示とツールの無効化設定を `-c` で渡すため、Codex は `--no-daemon` を指定し、共有バックグラウンドサーバーを使わない方式（embedded mode）で起動します。これにより、設定の上書きに伴う `Running without the shared background server` の警告を防ぎます。

現在はコンテナのファイアウォールを使い、Codex 自体は `--yolo` で起動します。Codex のサンドボックスによるローカルコマンドの通信制限と、OpenAI 側で実行する Web 検索・画像生成などの無効化は別の制御です。`minimal` の許可リストは feature flags を制御するもので、一覧に載らない機能や別途登録した MCP、許可済み API を使うコマンドまで一括して遮断するものではありません。許可先の MCP サーバーや `dp` の実行先が行う外部通信も、その実行先で制限する必要があります。

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

全設定の説明・既定値・複数行指定の可否を、コメント付きの [`example.aicontainer`](example.aicontainer) にまとめています。新規作成時はワークスペース直下の `.aicontainer` にコピーし、必要な設定行の先頭の `#` を外して使ってください。全設定をコメントにしているため、そのままコピーすると既定値で動作します。

```sh
cp example.aicontainer /path/to/project/.aicontainer
```

説明のコメントは設定行とは別の行に書いてください。設定変更後はAIコンテナを起動し直すと反映されます。

AIコンテナは常に、外部通信用の専用bridgeネットワーク（`public_network`）に参加します。Docker上の名前は起動ごとに `yumayo-ai-<小文字英数字のランダム値>` として生成し、コンテナ間通信を無効化します。AIコンテナ終了時にこの専用ネットワークも削除します。

強制終了などで専用ネットワークが残った場合は、次回起動時に未使用か確認して削除します。対象は `yumayo-ai-` に小文字英数字10文字が続く名前で、bridge・コンテナ間通信無効の設定が一致するものです。新しく作成する専用ネットワークには `io.yumayo.aicontainer.network=public` ラベルも付けます。ラベルのない従来の専用ネットワークも回収しますが、別の用途を示す同キーのラベルがあるものは除外します。

接続中のもの、停止中・作成済みを含むコンテナから参照されているもの、今回の `append-network` 指定先は削除しません。起動準備との競合を避けるため、作成から5分未満のものも保留し、それ以降の起動時に再確認します。状態や作成日時を確認できない場合は削除せず、削除に失敗した場合も警告を出して起動を続けます。この変更はホスト側の `.bash_ai_container` を更新し、`source .bash_ai_container` で読み込み直すと反映されます（`~/.bash_ai_container` にコピーしている場合はコピー先も更新）。イメージの再ビルドは不要です。

`append-network` を指定した場合は、専用ネットワークに加えて、その名前のDockerネットワークにも参加します。存在しなければbridgeネットワークを作成し、追加ネットワークは終了後も残します。MCPサーバーなどとネットワークを共有する場合に使用してください。同じキーを複数行書くと複数のネットワークに参加でき、重複した名前と空の定義は無視します。追加ネットワークでも、通信先はAPI・認証先、`allow-dns` に指定した名前から解決したIP、`allow-ip` に指定した範囲に制限します。

追加ネットワークはコンテナ起動時から接続し、外部APIへのデフォルト経路には専用ネットワークを使います。`append-network` を使う場合は、ゲートウェイ優先度（`gw-priority`）に対応する Docker Engine / CLI 28.0以降が必要です。

`append-network` 指定時は、プロキシやネットワークを作成する前にCLI・Engineのバージョンを確認します。28.0未満、バージョンの取得失敗、Docker API 1.48未満の場合は日本語のエラーを表示して起動を中止します。`DOCKER_API_VERSION` で古いAPIに固定している場合は、その指定も見直してください。`aicontainer dump` では出力したスクリプトの実行時に同じ確認を行います。

`aicontainer dump` の出力にも同じネットワーク準備・後始末を含みます。Ollamaモード（`aicontainer ollama` / `tool=claude-ollama`）は廃止済みで、指定すると起動前にエラーになります。

`docker-proxy-allow` と `docker-proxy-containers` は同じキーを複数行書いて指定します。未指定時はそれぞれ全コマンド・全コンテナを拒否します。従来のカンマ区切りの設定は、1項目につき1行へ書き換えてください。空の定義は無視します。

旧設定名 `network` は `append-network` に、`dns` は `allow-dns` に変更しました。旧設定名が残っている場合は起動時にエラーになります。既存の `.aicontainer` は設定名を書き換えてください。

`allow-dns` には、追加で接続を許可するコンテナ名・ネットワークエイリアス・外部ドメイン名を1行に1つ指定できます。コンテナ名やエイリアスを使う場合は、`append-network` に同じDockerネットワークを指定してください。

```
allow-dns=api.example.org
```

コンテナ起動時に、参加したネットワークのDNSを使ってIPv4アドレスを解決し、`/etc/hosts` とIP許可リストに登録します。接続先のコンテナは先に起動してください。名前解決に失敗した場合はAIコンテナの起動を中止します。モード別のAnthropic／OpenAIのAPI・認証先は自動で登録されます。

`allow-ip` には、追加で接続を許可するIPv4アドレスまたはCIDRを1行に1つ指定できます。`allow-dns` と併用できます。

```ini
allow-ip=192.168.1.10
allow-ip=172.20.0.0/16
```

プレフィックス長は0〜32で、`/32` は単一IP、`/0` はIPv4全体を許可します。CIDRのホスト部は無視されます（例: `192.168.1.10/24` は `192.168.1.0/24`）。前後の空白、引用符、行末の `#` コメントを使用できます。空の値、IPv6、ホスト名、先頭ゼロのある数値、不正なアドレス・プレフィックス長は起動前にエラーになります。

許可範囲への送信とその応答は、DNS（UDP/TCP 53番）を除く全ポートが対象です。localhost（`127.0.0.1` / `::1`）は追加設定なしで、53番を含む全ポートの送受信を許可します。Docker内蔵DNS、コンテナ自身の非ループバックIP、localhost以外のIPv6通信の拒否は引き続き優先します。`allow-ip` は名前解決やネットワークへの参加・経路を追加しません。他コンテナへ接続する場合は、必要に応じて `append-network` も指定してください。

localhost、API・認証先、`allow-dns` の解決先、`allow-ip` の範囲以外のIPは許可しません。設定や接続先のIPを更新した場合はAIコンテナを再作成してください。

既存環境で `allow-ip` を使うには、ホスト側で `bash install.sh main` を実行し、`source .bash_ai_container` で起動用関数を読み込み直してAIコンテナを再作成してください。`~/.bash_ai_container` にコピーしている場合は、そのファイルも更新してください。

### ローカルのMCPサーバーに接続する

HTTPで接続するMCPサーバーが `myproject` ネットワーク上で `mcp-server` という名前で動作する場合、`.aicontainer` に次を指定します。

```ini
append-network=myproject
allow-dns=mcp-server
```

AIツール側のMCP接続URLは、サーバーの設定に合わせて、例えば `http://mcp-server:3000/mcp` とします。MCPサーバーはコンテナ内で `0.0.0.0` に待ち受けさせてください。同じネットワーク経由で接続するため、ホストへのポート公開は不要です。`allow-dns` は接続許可の設定であり、AIツールへのMCP登録は別途必要です。

追加ネットワークはコンテナ間通信が可能なものを使用してください。外部APIへの通信は専用ネットワークを使うため、追加先には内部ネットワーク（`--internal`）も指定できます。`append-network` の指定だけではMCPへの通信は許可されず、`allow-dns` に指定したサーバーのIPへの通信を許可します。許可はIP単位で、DNSを除く全ポートが対象です。

### `.aimount` — 追加マウント

```
./data:/workspace/data
./reference:/workspace/reference:ro
./output:/workspace/output:rw
$HOME/.gitconfig:/workspace/.gitconfig:ro
${API_KEY?}:/workspace/api-key
```

形式は `ホストパス:コンテナパス[:ro|rw]` です。末尾に `:ro` を付けると読み取り専用、`:rw` または省略時は読み書き可能になります。ファイル・ディレクトリのどちらにも指定できます。

環境変数展開（`$VAR`, `${VAR:-default}`, `${VAR?}` 等）に対応。パスの空白・日本語・引用符はそのまま保持し、`$()` やバッククォートはコマンドとして実行しません。ワークスペース、`session`、`.aiignore` も同じパスを使用し、`session` の保存先は正規化した実パスでマウントします。

## Docker Socket Proxy（外部コンテナ連携）

`bash install.sh` でプロキシイメージも更新すると、次回の AI 起動時に旧イメージのプロキシを再作成します。既存の接続用ボリュームと許可設定は引き継ぎます。

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

実行先を固定する場合は、コマンドの前に `-p`（`--proxy-container-name`）を指定します。

```sh
dp --proxy-container-name workdir-dotnet-1 dotnet --version
dp -p workdir-dotnet-1 dotnet --version
```

`docker-proxy-containers` に登録した実際のコンテナ名を指定してください。`--proxy-container-name=workdir-dotnet-1` の形式も使えます。指定したコンテナが停止中、または実行ファイルが見つからない場合はエラーになり、別のコンテナでは実行しません。オプションを省略すると、従来どおり登録順に探索します。

実行先の作業ディレクトリは `-c`（`--cwd`）で指定できます。

```sh
dp -p workdir-dotnet-1 -c /path/to/current-directory dotnet --version
dp --cwd /path/to/current-directory dotnet test
```

パスは実行先のコンテナ内に存在するディレクトリを指定します。AIコンテナ内に同じディレクトリがある必要はありません。相対パスは、呼び出し時のAIコンテナの作業ディレクトリを基準に絶対パスへ変換します。省略時は現在のディレクトリを引き継ぎます。

### `dp` の動作

- `dp` に渡したコマンドは、AIコンテナ内のPATHを検索せず外部コンテナで実行します。`dp` は通常の実行ファイルなので、Bash以外のシェルやプログラムの `spawn` 等からも使用できます。
- 実行先を指定しない場合はコンテナを登録順に試し、転送先のPATHに実行ファイルがない場合だけ次のコンテナへ進みます。`--proxy-container-name` を指定した場合は、そのコンテナだけで実行します。実行後の終了コードが127でも再試行しません。
- 引数、標準入力、標準出力、標準エラー、終了コードを引き継ぎます。対話端末ではTTYも利用できます。
- 作業ディレクトリは既定でAIコンテナの現在のディレクトリを指定します。転送先にも同じ絶対パスでプロジェクトをマウントするか、`-c`（`--cwd`）で実行先のパスを指定してください。`.aiignore` は転送先のマウントには適用されません。
- 引数の指定が不正な場合は終了コード2、許可されていないコマンドや未登録コンテナの明示指定は126、PATHに見つからないコマンドや起動中の対象コンテナがない場合は127、プロキシ未設定・コンテナの登録がない場合・接続や実行開始のエラーは125になります。
- `dp` のオプションはコマンドの前に指定します。コマンド以降の引数は、`--help` や `-p`、`-c` なども含めて実行先にそのまま渡します。
- `dp /path/to/command` や `dp ./command` も転送先で実行します。引数をシェル文字列に変換せず渡すため、パイプやリダイレクトは呼び出し元のシェルで処理されます。

`docker-proxy-allow=npx playwright` は実際のコマンド列 `npx playwright ...` を許可する設定です。許可ルールに `dp` は付けません。`dp npx playwright test` と呼び出してください。

更新後は `bash install.sh main` でAIイメージを再ビルドしてAIコンテナを起動し直してください。今回の変更に伴うベースイメージやプロキシイメージの再ビルドは不要です。

`dp` の実装は `docker/aicontainer/docker-proxy/src/`、Goの単体テストは `docker/aicontainer/docker-proxy/test/` にあります。AIイメージのビルド時にGo用ステージでテスト・コンパイルし、`CGO_ENABLED=0` と `-trimpath -ldflags="-s -w"` で生成したバイナリだけを `/usr/local/bin/dp` にコピーします。Goのコンパイラやソースは実行用イメージに追加しません。

転送処理のテストはLinux上でGoを使い、Dockerなしで実行できます。`test/run.sh` は非公開関数をテストできるように、実装と単体テストを一時ディレクトリへ集めて実行し、終了時に削除します。Node.jsの結合テストは、一時ディレクトリにGo版 `dp` をビルドして実行します（テストの実行にはGoとNode.jsが必要です）。

```sh
sh docker/aicontainer/docker-proxy/test/run.sh
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

AIコンテナは常に外部通信用の専用ネットワークを使い、`append-network` を指定した場合は他コンテナのネットワークにも追加で参加します。すべてのネットワークで接続先をコンテナ内のファイアウォールにより制限します。外部コンテナのコマンド実行はDocker Socket Proxyを経由し、HTTPのMCPサーバーには `allow-dns` で許可して直接接続します。

接続先は、AIコンテナ内部のlocalhost（`127.0.0.1` / `::1`）、モード別のAnthropic／OpenAIのAPI・認証先、`.aicontainer` の `allow-dns` に指定した名前から解決したIPv4アドレス、`allow-ip` に指定したIPv4アドレス・CIDRに限定しています。localhostの送受信は、53番を含む全ポートで許可します。登録済みIP・範囲への送信とその応答は、DNS（UDP/TCP 53番）を除き全ポートで許可します。コンテナ自身の非ループバックIP、許可範囲外のIP、localhost以外のIPv6通信は拒否します。コンテナ内の開発サーバーやローカルMCPにはlocalhostで接続できます。

起動時に必要なドメインのIPv4アドレスを解決して `/etc/hosts` に登録し、その後はlocalhost以外へのDNS通信（UDP/TCP 53番ポート）とDocker内蔵DNSへの接続を拒否します。localhost上にDNSサーバーがある場合は問い合わせできます。IPアドレスを更新する場合はコンテナを再作成してください。

Dockerサブネット全体を自動で許可するルールはありません。Dockerホストのゲートウェイや他コンテナも、IP許可リストの範囲に含まれていなければ拒否します。`allow-dns` と `allow-ip` の設定は起動時のコピーを読み取り専用で渡すため、実行中にワークスペースの `.aicontainer` を編集しても許可先は増えません。

## アンインストール

```sh
bash uninstall.sh
```

## ライセンス

MIT
