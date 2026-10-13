# Windows / WSL Containers

WSL Containers の専用 VM に含まれる Docker Engine を使い、既存の Dockerfile と Bash ランチャーで AI コンテナを起動します。利用者が Docker Desktop / Docker Engine / Ubuntu ディストリビューションを手動インストールする手順を省きます。

```text
Windows の起動スクリプト
  └─ WSL Containers の専用 VM（内蔵 Docker Engine）
       ├─ 起動用コンテナ：既存の aicontainer 関数を実行
       ├─ 中継用コンテナ：起動用コンテナから内蔵 Docker への Unix ソケット接続
       └─ AI コンテナ：既存の Dockerfile、ENTRYPOINT、通信先制限を使用
```

起動用コンテナが内蔵 Docker に AI コンテナの作成を依頼します。起動用コンテナの中で別の Docker デーモンを動かす構成ではありません。AI コンテナへ直接渡す Docker ソケットは従来どおり、設定した場合の制限付き Docker Socket Proxy だけです。

**実験的な対応です。** WSL 3.0.1 を使った利用者の Windows 実環境で、内蔵 Docker を経由した AI コンテナの起動が確認されました。`wslc system session run docker ...` で内蔵エンジンを直接操作するため、WSL Containers の内部実装に依存します。通常の `wslc run` の機能だけで動く構成としては扱わないでください。

## 使い方

Windows 11 の 64 bit PowerShell で、このリポジトリのフォルダーから実行します。WSL 内の Bash からは実行しません。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-wsl.ps1 -Workspace C:\src\my-project -Tool codex
```

利用者の操作は次の流れです。

1. WSL が未導入なら、スクリプトが `wsl --install --no-distribution` を実行します。UAC を承認し、再起動の案内が出たら Windows を再起動します。
2. 同じコマンドをもう一度実行します。WSL Containers を準備し、初回は必要なイメージをビルドして AI を起動します。以後はこのコマンドで起動できます。

導入済みの WSL が古ければ `wsl --update` を実行します。プレビュー版は要求せず、安定版の WSL 3.0.1 以降を使用します。初回インストール時や更新が再起動を要求した場合は終了コード `3010` で停止します。自動再起動やログオン時の自動実行は登録しません。

WSL の実行ファイルはターミナルの PATH を優先して探し、見つからない場合は標準のインストール先を確認します。起動時に実行ファイルの場所、検出した WSL バージョン、`VirtualMachinePlatform` の状態を表示します。`wsl --version` が成功してもバージョンを読み取れない場合や、Windows 機能の状態が不明な場合は、自動インストールに進まず診断情報を表示して停止します。

WSL が導入済みでも `VirtualMachinePlatform` が無効なら、DISM でその機能と必要な親機能を有効化します。WSL 本体の再インストールは行いません。DISM のログは一時フォルダーの `aicontainer-vmp-<ID>.log` に保存し、そのパスを表示します。有効化に成功したら終了コード `3010` で停止するので、Windows を再起動して同じコマンドを実行してください。スクリプトからは自動再起動しません。

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

ドライブのルートから指定するときは、`-Workspace "C:\workspace\sample"` のように `C:` の直後に `\` を入れてください。`C:workspace\sample` は C ドライブの現在位置を基準とする相対パスなので、参照先が異なります。空白を含むパスは引用符で囲んでください。

通常権限の PowerShell から実行してください。管理者権限へ昇格するのは WSL の導入・更新と Windows 機能の有効化だけです。一般ユーザーと管理者の `wslc` は別のセッションを使うため、ビルドと起動は同じ権限で実行します。実行ポリシーの `Bypass` 指定はこの PowerShell プロセスだけに適用されます。

## 導入・ビルドだけを行う場合

```powershell
.\install-wsl.ps1 -Target setup   # WSL と Windows 機能を準備
.\install-wsl.ps1                 # ベース・AI・Proxy をビルド
.\install-wsl.ps1 -Target base
.\install-wsl.ps1 -Target main
.\install-wsl.ps1 -Target proxy
.\install-wsl.ps1 -Target rebuild # すべてキャッシュなし
wslc image list
```

既存の `docker/aicontainer/Dockerfile.base`、`docker/aicontainer/Dockerfile`、`docker/docker-proxy/Dockerfile` を変更せずに使用します。通常の全体ビルドはベースのキャッシュを利用し、メインと Proxy はキャッシュなしです。Docker Desktop / 別の WSL ディストリビューションにあるイメージは共有されません。

## ファイルと呼び出し関係

```text
start-wsl.ps1
└─ scripts/wsl-containers.ps1（Start-AiWslContainer）
   ├─ 中継用コンテナ
   │  └─ scripts/wsl-docker-relay/run-relay.sh
   │     └─ exec node docker-socket-relay.cjs <serve|check> ...
   └─ 起動用コンテナ
      └─ scripts/wsl-launch.sh
         └─ .bash_ai_container の aicontainer 関数
            └─ docker run で AI コンテナを起動
```

中継用のファイルは `scripts/wsl-docker-relay/` にまとめています。このディレクトリだけを中継用コンテナの `/aicontainer-relay` に読み取り専用でマウントします。

| ファイル | 役割 |
| --- | --- |
| `run-relay.sh` | nvm を読み込んで Node.js の PATH を設定し、引数をそのまま `docker-socket-relay.cjs` へ渡します。`exec` するため、Bash は Node.js プロセスに置き換わります。 |
| `docker-socket-relay.cjs` | `serve` は Unix ソケット間の双方向バイト中継、`check` は中継経由の HTTP `GET /_ping` による起動確認です。`.cjs` は CommonJS 形式の Node.js モジュールを表します。 |

`serve` は Docker API のリクエストを解釈せず、ストリームをそのまま中継します。HTTP Upgrade / hijack 後の対話通信、half-close、双方向の backpressure を扱います。`check` は別プロセスとして `docker exec` で実行し、既定で 10 秒間再試行します。各リクエストにも 1 秒のタイムアウトを設けています。

同じ中継は Python の `socket` / `asyncio` でも実装できます。Node.js を選んだ理由は、既存の `Dockerfile.base` が nvm 経由で Node.js 24 を明示的に導入しているためです。Python は明示的な導入対象ではなく、追加依存とイメージの再ビルドを避けるために Node.js の標準モジュールを使用しています。

## 起動処理と通信制限

`wslc run` は `--cap-add` を提供していないため、そのままでは既存のファイアウォールを初期化できません。そこで内蔵 Docker Engine の `docker` コマンドを使い、既存の `--cap-add=NET_ADMIN --cap-add=NET_RAW` を維持します。AI 起動時のファイアウォール初期化と疎通検証は既存の `ENTRYPOINT` が行います。

起動前に、作業ファイルを渡さない使い捨てコンテナで必要な権限と `iptables` / `ip6tables` / `ipset` の利用を確認します。失敗した場合は停止し、通信制限を外しての起動へは切り替えません。

Windows のフォルダーはまず `wslc` で共有し、Docker の `inspect` が返す VM 側のパスを使います。Windows の `C:\...` を Docker デーモンへそのまま渡したり、`/mnt/c` の存在を仮定したりしません。起動用コンテナと内蔵 Docker で同じパスを使うことで、既存ランチャーが作るマウントや許可設定の読み取り専用コピーを利用します。

`system session run` は対話用 TTY を作らないため、起動用コンテナは `wslc create --interactive --tty` で作成し、`wslc start --attach --interactive` で端末を接続します。作成・開始・削除を `wslc` にそろえます。以前の「内蔵 Docker で作成し、`wslc` で開始する」方式では、実機で `E_UNEXPECTED` が発生し、コンテナが `created` のまま残りました。

ワークスペースとリポジトリは、起動用コンテナにも Windows のパスを `--volume` で渡して共有します。マウント先には、先に共有用コンテナの `inspect` で取得した VM 側のパスを指定します。共有用コンテナは起動中ずっと保持するため、Docker が AI コンテナに渡す元の VM パスも有効なままです。リポジトリはワークスペースと同じ共有元の場合を除いて読み取り専用です。

Linux 内で共有するデータには、`wslc volume create --driver guest` で作る専用ボリュームを使います。Docker で作ったボリューム名は WSL 側では登録されておらず、`WSLC_E_VOLUME_NOT_FOUND` になります。また、`guest` は Docker の `local` ドライバーとは異なり、実機で `--opt type=none` が拒否されました。`type=none` / `o=bind` / `device=...` は指定しません。

ボリュームは次の 2 つです。どちらも共有用コンテナにマウントし、そのコンテナの Docker `inspect` から実際の VM 側パスを取得します。WSL と Docker で同じボリューム名が使えるとは仮定しません。

- `yumayo-ai-tmp-<ID>`：IP・DNS 許可設定の一時ファイル用です。起動用コンテナにも VM と同じパスでマウントし、`AICONTAINER_RUNTIME_ROOT` で Bash ランチャーに伝えます。通常の Bash 起動は従来どおり `/tmp` を使います。
- `yumayo-ai-engine-<ID>`：起動用コンテナだけが使う Docker 接続の中継ソケット用です。起動用コンテナでは短いパス `/aicontainer-engine/docker.sock` で参照し、Unix ソケットのパス長制限を避けます。

準備用の Docker コンテナで、両ボリュームの所有者をイメージの `ubuntu` の UID に、ディレクトリのモードを `0700` に設定します。未対応の `volume-nocopy` は使用しません。マウント先はベースイメージに存在しない専用パスなので、イメージ内の `/tmp` の内容をコピーしたり、削除したりする必要はありません。

中継用コンテナ `yumayo-ai-relay-<ID>` は内蔵 Docker で作成し、生の Docker ソケットと専用ボリュームを接続します。上記の `run-relay.sh` から `docker-socket-relay.cjs` を起動し、API と対話入出力のバイト列を Unix ソケット間で中継します。通信先を制限する AI 用 Docker Socket Proxy とは別の、信頼された起動処理です。中継用コンテナはネットワークなし・非 root で動き、ソケットはモード `0600` にします。Docker ソケットへの接続に必要な実際の GID を使用し、Windows 向け TCP ポートは開きません。AI 起動前に中継経由の `/_ping` を確認し、失敗したら停止して中継のログを表示します。

起動用コンテナには中継ボリュームを読み取り専用で渡します。生の Docker ソケットと中継ボリュームは、AI コンテナには渡しません。AI の通信先制限と、設定した場合の制限付き Docker Socket Proxy は既存の Bash ランチャーが準備します。

終了時は、起動用コンテナ、中継用コンテナ、所有者ラベルで特定した AI コンテナと専用ネットワーク、共有用コンテナ、最後に 2 つの WSL ボリュームを削除します。`wslc volume remove --force` も使用中のボリュームは削除できないため、参照元のコンテナを先に削除します。途中で失敗した場合も、その起動で作成済みのリソースだけを回収します。ボリューム削除によって一時ファイルとソケットは消えますが、Windows 側の作業ファイルは残ります。他のコンテナ、追加ネットワーク、継続利用する Proxy は削除しません。

Windows の強制終了などで後始末できなかった場合は、`wslc container list --all` で `yumayo-ai-share-*` / `yumayo-ai-launch-*`、`wslc system session run docker ps -a` で `yumayo-ai-relay-*`、`wslc volume list` で `yumayo-ai-engine-*` / `yumayo-ai-tmp-*` を確認できます。古い実装が作成した Docker ボリュームは別管理なので、`wslc system session run docker volume ls` で確認します。

`Starting interactive launcher` の後で失敗した場合は、起動用コンテナの作成後、端末の接続または起動したプロセスで失敗しています。削除する前に Docker の実行状態を `Launcher state before cleanup` として表示します。`Status=created` ならコンテナ内の起動処理はまだ始まっていません。`E_UNEXPECTED` だけでは原因を特定できないため、WSL 側の終了コードとこの状態を合わせて確認してください。診断のために読み取る情報は `.State` に限定し、環境変数を含む inspect 全体は表示しません。

起動前に表示される `-P INPUT ACCEPT` などは、作業フォルダーを持たない権限確認用の一時コンテナの出力です。AI コンテナのファイアウォールの状態を表示したものではありません。エラー後の `yumayo-ai-launch-*` / `yumayo-ai-share-*` の表示は削除コマンドの出力で、AI の起動成功を意味しません。

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

`VirtualMachinePlatform` の有効化に失敗した場合は、表示された DISM ログを確認してください。操作結果をターミナルで確認するには、管理者として開いた PowerShell で次を実行します。既定のログ保存先は `%WINDIR%\Logs\DISM\dism.log` です。

```powershell
dism.exe /Online /Enable-Feature /FeatureName:VirtualMachinePlatform /All /NoRestart
```

成功後は Windows を再起動し、通常権限の PowerShell で起動スクリプトを再実行します。`wsl --version` は WSL 本体のバージョン確認であり、仮想化機能が動作することまでは確認しません。仮想マシンでは、Windows 機能を追加できることと、ホストが入れ子の仮想化を許可していることを確認してください。クリーンインストールを繰り返し検証する場合は、後述の仮想マシンとチェックポイントを使う方法を推奨します。

DISM が終了コード `12006`、ログに `0x80072EE6` を出した場合は、Windows の機能追加処理で URL のスキームを認識できなかったことを示します。これだけでは、どの URL や設定が原因かは分かりません。通常の Windows で発生した場合は、Windows Update / WSUS とオプション機能の取得元設定、および CBS ログを確認します。次は設定を変更しない確認コマンドです。CBS ログの読み取りでアクセスが拒否される場合は、管理者の PowerShell で実行してください。

```powershell
Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate' -ErrorAction SilentlyContinue |
    Select-Object WUServer, WUStatusServer
Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -ErrorAction SilentlyContinue |
    Select-Object UseWUServer
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Servicing' -ErrorAction SilentlyContinue |
    Select-Object LocalSourcePath
Select-String -Path "$env:WINDIR\Logs\CBS\CBS.log" -Pattern '80072ee6', '12006' -Context 3,3 |
    Select-Object -Last 5
```

レジストリの項目が存在しなければ、その確認コマンドは何も表示しません。`WUServer` に URL が設定されている場合は、`http://` または `https://` を含む正しい取得元か確認してください。企業などで管理されている設定は管理者に確認します。スクリプトは取得元の変更や Windows Update のリセットを自動実行しません。`-WebDownload` は WSL 本体の配布経路を指定するためのオプションで、この DISM エラーの対処にはなりません。

DISM ログの `Encountered an unknown option "featurename"` が `Info` として出ていても、その行だけで `/FeatureName` が誤りとは判断できません。後続の `Error` と HRESULT を確認してください。また、`No Sandbox was created, DISM running in-place` は DISM 自身の作業方式の記録で、Windows サンドボックス内かどうかを示すものではありません。

`.gitattributes` で Linux 向けの Dockerfile とシェルスクリプトを LF に固定します。古い作業コピーに CRLF が残っていた場合は、ファイル名を表示して停止します。エディターで LF に変更するか、変更を退避して新しい作業コピーを取得してください。

```powershell
# WSL の導入やネットワーク通信をしない PowerShell モックテスト
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\test\test_wsl_containers.ps1
```

```sh
# Linux 上の Bash / Docker モックと Unix ソケット中継のテスト（Node.js が必要）
python3 -m unittest discover -s test -p 'test_*.py'
```

実機では未導入時の UAC・再起動、旧版からの更新、イメージビルド、WSL ボリュームと Windows フォルダーの共有、中継経由の Docker API 接続、`wslc` で作成した起動用コンテナへの端末接続、ファイアウォールの疎通検証、セッションの保存、終了時の後始末を通して確認する必要があります。

## WSL の導入を繰り返し検証する

ホスト PC の WSL を削除せずに未導入状態を再現するには、通常の Windows 11 仮想マシンで導入前のチェックポイントを保存します。Windows サンドボックスは通常の Windows インストールと機能追加の条件が異なるため、そこでの DISM の失敗だけでは通常環境での導入可否を判断できません。

| 確認する内容 | 検証環境 | 確認できる範囲 |
| --- | --- | --- |
| 未導入・導入済み・旧版・機能無効・セットアップ失敗時の分岐 | Windows サンドボックスや通常の Windows で既存の PowerShell モックテストを実行 | WSL、DISM、Docker を実行せずにスクリプトの判断と呼び出し内容を確認 |
| WSL の実際の導入、UAC、Windows 機能の有効化、再起動後の継続 | 入れ子の仮想化を有効にした Windows 11 VM | OS のセットアップから再起動までを確認 |
| イメージのビルドと AI コンテナの起動 | WSL 導入済みの同じ VM | 実際の WSL Containers と Docker Engine を使って確認 |

モックテストは、WSL が未導入でも導入済みでも同じコマンドで実行できます。モックの成功は実際のインストールや AI 起動の成功を保証するものではありません。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\test\test_wsl_containers.ps1
```

Hyper-V を利用できるホストでは、次の手順で通しの検証を繰り返せます。対応する CPU と Windows 構成が必要です。VM の作成や以下の手順はスクリプトから自動実行しません。

1. 検証用の Windows 11 VM を作成し、Windows の初期設定を済ませます。WSL を導入する前にゲストをシャットダウンします。
2. ホストの管理者 PowerShell で、停止中の検証用 VM に入れ子の仮想化を許可し、導入前の状態を保存します。`AIContainer-Test` は実際の検証用 VM 名に置き換えてください。

   ```powershell
   Set-VMProcessor -VMName 'AIContainer-Test' -ExposeVirtualizationExtensions $true
   Checkpoint-VM -Name 'AIContainer-Test' -SnapshotName 'before-wsl'
   Start-VM -Name 'AIContainer-Test'
   ```

3. ゲストにリポジトリをコピーし、通常権限の PowerShell から次を実行します。WSL 本体や機能の導入で再起動を案内されたら、ゲストを再起動して同じコマンドを繰り返し、セットアップが終了コード `0` で完了することを確認します。

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-wsl.ps1 -Target setup
   $LASTEXITCODE
   ```

4. 導入後のゲストをシャットダウンし、ホストで `wsl-ready` という別のチェックポイントも保存します。その状態を使うと、イメージビルドや AI 起動のテストを WSL 導入からやり直す必要がありません。ゲストを起動し、既存のプロジェクトを指定して `start-wsl.ps1` を実行します。
5. 導入を再検証するときは、必要なログと変更を VM の外へ保存し、ゲストをシャットダウンしてからホストで次を実行します。チェックポイントの復元では、保存時点より後のゲスト内の変更が失われます。

   ```powershell
   Restore-VMSnapshot -VMName 'AIContainer-Test' -Name 'before-wsl'
   Start-VM -Name 'AIContainer-Test'
   ```

この環境では PowerShell と Hyper-V を実行できないため、モックテストの実行と上記 VM 手順の動作確認は未実施です。

## 参照

- [Microsoft Learn: WSL container](https://learn.microsoft.com/en-us/windows/wsl/wsl-container)
- [Microsoft Learn: WSL の基本的なコマンド](https://learn.microsoft.com/ja-jp/windows/wsl/basic-commands)
- [Microsoft WSL: system session run の実装](https://github.com/microsoft/WSL/blob/master/src/windows/wslc/commands/SessionRunCommand.cpp)
- [Microsoft WSL: セッション実行と端末処理の実装](https://github.com/microsoft/WSL/blob/master/src/windows/wslc/services/SessionService.cpp)
- [Microsoft WSL: 内蔵 Docker と Windows パス共有の制約](https://github.com/microsoft/WSL/issues/40957)
- [Microsoft WSL: Docker Engine API の公開に関する要望](https://github.com/microsoft/WSL/issues/40976)
