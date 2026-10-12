# Windows / WSL Containers

WSL Containers の専用 VM に含まれる Docker Engine を使い、既存の Dockerfile と Bash ランチャーで AI コンテナを起動します。利用者が Docker Desktop / Docker Engine / Ubuntu ディストリビューションを手動インストールする手順を省きます。

```text
Windows の起動スクリプト
  └─ WSL Containers の専用 VM（内蔵 Docker Engine）
       ├─ 起動用コンテナ：既存の aicontainer 関数を実行
       └─ AI コンテナ：既存の Dockerfile、ENTRYPOINT、通信先制限を使用
```

起動用コンテナが内蔵 Docker に AI コンテナの作成を依頼します。起動用コンテナの中で別の Docker デーモンを動かす構成ではありません。AI コンテナへ直接渡す Docker ソケットは従来どおり、設定した場合の制限付き Docker Socket Proxy だけです。

**実験的な対応です。Windows 実機での導入・ビルド・対話起動は未検証です。** `wslc system session run docker ...` で内蔵エンジンを直接操作するため、WSL Containers の内部実装に依存します。通常の `wslc run` の機能だけで動く構成としては扱わないでください。

## 使い方

Windows 11 の 64 bit PowerShell で、このリポジトリのフォルダーから実行します。WSL 内の Bash からは実行しません。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-wsl.ps1 -Workspace C:\src\my-project -Tool codex
```

利用者の操作は次の流れです。

1. WSL が未導入なら、スクリプトが `wsl --install --no-distribution` を実行します。UAC を承認し、再起動の案内が出たら Windows を再起動します。
2. 同じコマンドをもう一度実行します。WSL Containers を準備し、初回は必要なイメージをビルドして AI を起動します。以後はこのコマンドで起動できます。

導入済みの WSL が古ければ `wsl --update` を実行します。プレビュー版は要求せず、安定版の WSL 3.0.1 以降を使用します。初回インストール時や更新が再起動を要求した場合は終了コード `3010` で停止します。自動再起動やログオン時の自動実行は登録しません。

```powershell
# .aicontainer の tool 設定を使用。設定がなければ Claude Code
.\start-wsl.ps1 -Workspace C:\src\my-project

# Claude Code / Codex CLI / Bash を指定
.\start-wsl.ps1 -Workspace C:\src\my-project -Tool claude
.\start-wsl.ps1 -Workspace C:\src\my-project -Tool codex
.\start-wsl.ps1 -Workspace C:\src\my-project -Tool bash

# Dockerfile や AI ツールを更新したいとき
.\start-wsl.ps1 -Workspace C:\src\my-project -Rebuild

# WSL を Microsoft Store 以外の配布経路から導入・更新する環境向け
.\start-wsl.ps1 -Workspace C:\src\my-project -WebDownload
```

`-Workspace` は既存のディレクトリを指定します。省略時は現在のディレクトリを使います。認証情報と履歴は、そのプロジェクトの `.claude.local` / `.codex.local` に保存します。イメージがそろっている場合、次回起動ではビルドを省略します。

通常権限の PowerShell から実行してください。管理者権限へ昇格するのは WSL の導入・更新だけです。一般ユーザーと管理者の `wslc` は別のセッションを使うため、ビルドと起動は同じ権限で実行します。実行ポリシーの `Bypass` 指定はこの PowerShell プロセスだけに適用されます。

## 導入・ビルドだけを行う場合

```powershell
.\install-wsl.ps1 -Target setup   # WSL の導入・更新だけ
.\install-wsl.ps1                 # ベース・AI・Proxy をビルド
.\install-wsl.ps1 -Target base
.\install-wsl.ps1 -Target main
.\install-wsl.ps1 -Target proxy
.\install-wsl.ps1 -Target rebuild # すべてキャッシュなし
wslc image list
```

既存の `docker/aicontainer/Dockerfile.base`、`docker/aicontainer/Dockerfile`、`docker/docker-proxy/Dockerfile` を変更せずに使用します。通常の全体ビルドはベースのキャッシュを利用し、メインと Proxy はキャッシュなしです。Docker Desktop / 別の WSL ディストリビューションにあるイメージは共有されません。

## 起動処理と通信制限

`wslc run` は `--cap-add` を提供していないため、そのままでは既存のファイアウォールを初期化できません。そこで内蔵 Docker Engine の `docker` コマンドを使い、既存の `--cap-add=NET_ADMIN --cap-add=NET_RAW` を維持します。AI 起動時のファイアウォール初期化と疎通検証は既存の `ENTRYPOINT` が行います。

起動前に、作業ファイルを渡さない使い捨てコンテナで必要な権限と `iptables` / `ip6tables` / `ipset` の利用を確認します。失敗した場合は停止し、通信制限を外しての起動へは切り替えません。

Windows のフォルダーはまず `wslc` で共有し、Docker の `inspect` が返す VM 側のパスを使います。Windows の `C:\...` を Docker デーモンへそのまま渡したり、`/mnt/c` の存在を仮定したりしません。起動用コンテナと内蔵 Docker で同じパスを使うことで、既存ランチャーが作るマウントや許可設定の読み取り専用コピーを利用します。

`system session run` は対話用 TTY を作らないため、起動用コンテナを内蔵 Docker で作成し、`wslc start --attach --interactive` で端末を接続します。起動用コンテナはイメージの `ubuntu` ユーザーで動かし、セッションファイルの所有者を AI とそろえます。

終了時は、その起動で作成した起動用コンテナ・AI コンテナ・専用ネットワーク・フォルダー共有用コンテナを後始末します。所有者ラベルで対象を絞り、他のコンテナ、追加ネットワーク、継続利用する Proxy は削除しません。Windows 自体の強制終了などで後始末できない場合は、`wslc container list --all` で `yumayo-ai-share-*` / `yumayo-ai-launch-*` を確認できます。

## 現段階の範囲

- `.aicontainer`、`.aiignore`、`.aimount` の処理は既存の Bash ランチャーを使用します。
- 共有する Windows 側のデータは `-Workspace` のフォルダーだけです。`session` や `.aimount` は、その配下への相対パスを使用してください。Windows ホーム、兄弟ディレクトリ、別 WSL ディストリビューションの Linux 絶対パスは自動共有しません。
- AI 内の作業パスには、WSL が生成する `/mnt/...` の共有パスを使います。このパスは起動ごとに変わる場合があります。認証ファイルと履歴は Windows 側に残りますが、ツールが作業パスを履歴の識別に使う場合の継続動作は実機確認が必要です。
- `before-start-up` は通信なしの起動用 Linux コンテナ内で実行します。Windows の PowerShell や、利用者の通常の WSL 環境で実行するものではありません。
- `append-network` は既存と同様に Docker Engine / CLI 28.0 以降が必要です。WSL 同梱の Docker が古い場合は、既存のバージョン確認で停止します。同梱エンジンの差し替えは行いません。
- Docker Socket Proxy の実行先は同じ WSL Containers セッションの Docker に存在するコンテナです。Docker Desktop や別の WSL のコンテナには接続しません。外部コンテナとの同一パス共有は別途設定が必要です。
- 内蔵 Docker で直接作成するコンテナの Windows 向けポート転送は今回の対象外です。

## 前提と検証

BIOS / UEFI のハードウェア仮想化が有効である必要があります。仮想マシン内では入れ子の仮想化も必要です。初回の導入・ビルドには WSL 配布元、コンテナレジストリ、既存 Dockerfile の apt / npm / GitHub / AI CLI 配布元への通信が必要です。

`.gitattributes` で Linux 向けの Dockerfile とシェルスクリプトを LF に固定します。古い作業コピーに CRLF が残っていた場合は、ファイル名を表示して停止します。エディターで LF に変更するか、変更を退避して新しい作業コピーを取得してください。

```powershell
# WSL の導入やネットワーク通信をしない PowerShell モックテスト
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\test\test_wsl_containers.ps1
```

```sh
# Linux 上の Bash / Docker モックを使う起動テストと既存テスト
python3 -m unittest discover -s test -p 'test_*.py'
```

実機では未導入時の UAC・再起動、旧版からの更新、イメージビルド、内蔵 Docker で作成したコンテナへの `wslc` の端末接続、ファイアウォールの疎通検証、セッションの保存、終了時の後始末を確認する必要があります。

## 参照

- [Microsoft Learn: WSL container](https://learn.microsoft.com/en-us/windows/wsl/wsl-container)
- [Microsoft Learn: WSL の基本的なコマンド](https://learn.microsoft.com/ja-jp/windows/wsl/basic-commands)
- [Microsoft WSL: system session run の実装](https://github.com/microsoft/WSL/blob/master/src/windows/wslc/commands/SessionRunCommand.cpp)
- [Microsoft WSL: セッション実行と端末処理の実装](https://github.com/microsoft/WSL/blob/master/src/windows/wslc/services/SessionService.cpp)
- [Microsoft WSL: 内蔵 Docker と Windows パス共有の制約](https://github.com/microsoft/WSL/issues/40957)
- [Microsoft WSL: Docker Engine API の公開に関する要望](https://github.com/microsoft/WSL/issues/40976)
