# インストール方法

```sh
bash install.sh
```

# 起動方法

```sh
source .bash_ai_container
aicontainer
```

# aicontainerの使い方（3パターン）

このリポジトリでは、用途に応じて以下の3つの起動方法があります。

```sh
# 1) Claude Code（デフォルト）
aicontainer

# 2) Codex CLI
aicontainer codex

# 3) Claude Code + Ollama
aicontainer ollama gpt-oss:20b
```

それぞれの用途は以下の通りです。

- `aicontainer`：Claude Code を起動します（.claude.local を使用）
- `aicontainer codex`：Codex CLI を起動します（.codex.local を使用）
- `aicontainer ollama gpt-oss:20b`：Claude Code を Ollama 経由でモデルを起動します（.claude.ollama を使用）

# AIが使用する記憶領域について

このリポジトリでは、AIが使用する記憶領域（認証情報やチャット履歴など）をプロジェクトごとに分離します。
用途に応じて以下のディレクトリが作成され、コンテナ内にマウントされます。

- `.claude.local`：Claude Code 用の記憶領域
- `.claude.ollama`：Claude Code + Ollama 用の記憶領域
- `.codex.local`：Codex CLI 用の記憶領域

それぞれの領域を分離することで、プロジェクト間で状態や認証情報が混ざらないようにしています。

# .aiignoreについて

.aiignoreファイルを作成することで、コンテナ内でAIに見せたくないファイルやディレクトリを指定できます。

## 基本的な使い方

任意のディレクトリに`.aiignore`ファイルを作成し、1行に1つの除外パターンを記述します。

```
# コメント行（#で始まる行は無視されます）

# 機密情報を含むファイル
secret.txt
.env

# ディレクトリ全体を除外（配下のファイルも全て除外されます）
node_modules
.git

# サブディレクトリ内のファイルを指定
config/secrets.json
data/private
```

## 動作の仕組み

### パスの解釈

- .aiignoreファイルに記述されたパスは、そのファイルがあるディレクトリからの相対パスとして解釈されます
- 先頭の`/`は自動的に削除されます（正規化）
- 例：`/path/to/data`と`path/to/data`は同じ意味になります

### ファイルとディレクトリの扱い

- ファイルを指定した場合は、そのファイルが`/dev/null`にバインドマウントされ、コンテナ内では空ファイルとして見えます
- ディレクトリを指定した場合は、そのディレクトリが空のtmpfsでマウントされ、コンテナ内では空ディレクトリとして見えます
- ディレクトリを指定すると、その配下のファイルやサブディレクトリも全て除外されます

### 再帰的な処理

- プロジェクト内の全ての.aiignoreファイルが自動的に検出され、処理されます
- サブディレクトリにも.aiignoreファイルを配置でき、それぞれが独立して機能します
- 親ディレクトリと子ディレクトリの.aiignoreで同じパスが指定されても問題ありません（重複チェックあり）

## 注意点

### .aimountとの連携

- .aimountでマウントしたディレクトリ内の.aiignoreファイルも自動的に処理されます
- マウント先のコンテナパスに対して.aiignoreのルールが適用されます

### 相対パスの基準

- 各.aiignoreファイルで指定するパスは、そのファイルが置かれているディレクトリが基準になります
- プロジェクトルートの.aiignoreとサブディレクトリの.aiignoreでは、相対パスの基準が異なります

### blobパターンは未サポート

- 現在の実装では`*.log`や`test_*.py`のようなblobパターンは使用できません
- 明示的なファイル名またはディレクトリ名を指定する必要があります

### 重複指定

- 複数の.aiignoreファイルで同じパスが指定された場合、最初に処理されたものが適用されます
- 重複した指定は自動的にスキップされ、メッセージが表示されます

## 例

### プロジェクトルートの.aiignore

```
# 開発時の一時ファイル
.env
.env.local

# 依存関係
node_modules
venv

# ビルド成果物
dist
build

# 機密情報
credentials.json
private_keys
```

### サブディレクトリの.aiignore（例：data/.aiignore）

```
# このディレクトリ内の機密データ
secrets.csv
personal_info.json

# サブディレクトリ全体を除外
raw_data
temp
```

### 階層的な使用例

```
project/
├── .aiignore                  # ルートレベルの除外設定
├── src/
│   └── config/
│       └── .aiignore          # config/配下の除外設定
└── data/
    ├── .aiignore              # data/配下の除外設定
    └── sensitive/
        └── .aiignore          # data/sensitive/配下の除外設定
```

各.aiignoreファイルは独立して動作し、それぞれのディレクトリ配下のファイルを制御します。

# .aicontainerについて

.aicontainerファイルを作成することで、Dockerネットワーク名のカスタマイズやセッション（記憶領域）の共有パスを設定できます。

## 基本的な使い方

プロジェクトルートに`.aicontainer`ファイルを作成し、`KEY=VALUE`形式で設定を記述します。

```
# Dockerネットワーク名のカスタマイズ
network=myproject

# セッション（記憶領域）の共有パス
session=../shared
```

## 設定項目

### network

Dockerネットワーク名をカスタマイズします。

- 未指定の場合、ネットワーク名は`yumayo-ai`になります
- 指定した場合、ネットワーク名は`yumayo-ai-{値}`になります
- 例：`network=test`の場合、ネットワーク名は`yumayo-ai-test`になります

### session

セッション（記憶領域）の共有パスを指定します。

- 未指定の場合、カレントディレクトリに記憶領域（`.claude.local`など）が作成されます
- 指定した場合、指定パスに記憶領域が作成され、`~/.claude`もそのパスから共有されます
- 相対パスはカレントディレクトリからの相対パスとして解釈されます
- 絶対パスも使用できます
- 指定パスが存在しない場合は自動的に作成されます

## 動作の仕組み

### ファイルのフォーマットルール

- `#`で始まる行はコメントとして無視されます
- 空行は無視されます
- 値をダブルクォート（`"`）またはシングルクォート（`'`）で囲むことができます（クォートは自動的に除去されます）
- キー名には英数字とアンダースコアが使用できます

### networkの動作

- 指定した値に`yumayo-ai-`プレフィックスが付与され、Dockerネットワーク名として使用されます
- 該当するネットワークが存在しない場合は自動的に作成されます
- プロジェクトごとに異なるネットワーク名を指定することで、ネットワークを分離できます

### sessionの動作

- 指定したパスに記憶領域ディレクトリ（`.claude.local`、`.claude.ollama`、`.codex.local`）が作成されます
- 相対パスの場合、カレントディレクトリと結合した後、`realpath`で正規化されます
- 複数プロジェクトで同じsessionパスを指定することで、記憶領域を共有できます

## 例

```
# プロジェクト専用のネットワークを使用
network=myproject

# 親ディレクトリでセッションを共有
session=../
```

```
# ネットワーク名のみカスタマイズ
network=test
```

```
# 絶対パスでセッションを指定
session=/home/user/shared-session
```

## ユースケース

### 複数プロジェクトでセッションを共有する

複数のプロジェクトで同じ認証情報やチャット履歴を使いたい場合、共通の親ディレクトリをsessionに指定します。

```
project-a/.aicontainer → session=../
project-b/.aicontainer → session=../
```

これにより、両プロジェクトの記憶領域が親ディレクトリに作成され、共有されます。

### プロジェクトごとにネットワークを分離する

複数のプロジェクトを同時に起動する場合、ネットワーク名を分けることでコンテナ間の通信を分離できます。

```
project-a/.aicontainer → network=project-a
project-b/.aicontainer → network=project-b
```

# .aimountについて

.aimountファイルを作成することで、コンテナに追加のディレクトリやファイルをマウントできます。

## 基本的な使い方

プロジェクトルートに`.aimount`ファイルを作成し、1行に1つのマウント設定を記述します。

```
# 形式: ホストパス:コンテナパス
./data:/workspace/data
/home/user/configs:/workspace/configs
```

## 環境変数の使用

docker-compose.ymlと同じように環境変数を使用できます。

```
# 基本的な環境変数展開
$HOME/.config:/workspace/config
${PWD}/data:/workspace/data

# エラーハンドリング（未定義の場合にエラー）
${API_KEY?}:/workspace/api-key
${DATABASE_URL:?Database URL is required}:/workspace/db-config

# デフォルト値の使用
${DATA_DIR:-./data}:/workspace/data
${CONFIG_FILE:-/etc/default.conf}:/workspace/config
```

## 環境変数の構文

bashのパラメータ展開構文がサポートされています：

- `$VAR` または `${VAR}` - 変数を展開
- `${VAR?}` - 変数が未定義ならエラーで処理中断
- `${VAR:?}` - 変数が未定義または空ならエラーで処理中断
- `${VAR?error message}` - カスタムエラーメッセージ付き
- `${VAR:-default}` - 変数が未定義または空ならデフォルト値を使用
- `${VAR-default}` - 変数が未定義ならデフォルト値を使用

## 注意点

### エラー時の動作

- 環境変数のエラー（`${VAR?}`など）が発生した場合、処理は即座に中断されコンテナは起動しません
- これはdocker-compose.ymlと同じ動作です
- フォーマットエラー（`:`が見つからないなど）も同様に処理を中断します

### マウント元のパス

- 相対パスは.aimountファイルが置かれているディレクトリからの相対パスとして解釈されます
- 絶対パスも使用できます
- マウント元のパスが存在しない場合はエラーになります

### .aiignoreとの連携

- .aimountでマウントしたディレクトリ内の.aiignoreファイルも自動的に処理されます
- マウント先のパスに対して.aiignoreのルールが適用されます

### パスに含まれるコロン

- 環境変数展開後のパスにコロン（`:`）が含まれる場合、最初のコロンが区切り文字として使用されます
- 例：`/path/with:colon:/dest` の場合、`/path/with` がソース、`colon:/dest` がデスティネーションとして解釈されます
- このような場合は環境変数を使って回避できます：`${SOURCE}:${DEST}`

## 例

```
# コメント行（#で始まる行は無視されます）

# プロジェクトデータをマウント
./data:/workspace/data

# ホームディレクトリの設定ファイル
$HOME/.gitconfig:/workspace/.gitconfig
$HOME/.ssh:/workspace/.ssh

# 環境変数で管理されたパス（必須）
${PROJECT_ROOT?Project root must be set}:/workspace/project

# 環境変数で管理されたパス（オプション、デフォルト値あり）
${CACHE_DIR:-./cache}:/workspace/cache
```

# おすすめ

.bash_ai_container を ~/.bash_ai_containerにコピーします。

~/.bashrc に下記のプログラムを追記します。

```sh
if [ -f ~/.bash_ai_container ]; then
    . ~/.bash_ai_container
fi
```

以降は、aicontainerとコマンドを実行することで、任意の場所でaiコンテナを起動することができます。

# Ollamaで動かす

別リポジトリの docker compose up -d を先に実行してください。  
https://github.com/yumayo/nginx-open-webui-ollama

あとは以下のコマンドで実行可能です。  
こちらは.claude.ollamaディレクトリが作成されます。

```sh
aicontainer ollama gpt-oss:20b
```
