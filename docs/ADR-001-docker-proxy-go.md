# ADR-001: Docker Socket ProxyをnginxからGoに移行

## ステータス

採用済み (2026-03-21)

## コンテキスト

AIコンテナから外部コンテナ（Playwright等）を `docker compose exec` で操作するために、Docker Socket Proxyを導入していた。初期実装はnginx + njsで、APIエンドポイントのフィルタリングとexecコマンドの許可リスト制御を行っていた。

しかし、`docker exec` のstdout/stderrがプロキシ経由で取得できない問題が発生した。

## 問題の原因

Docker exec APIは `exec/start` エンドポイントで **HTTP接続ハイジャック** を使用する:

1. クライアントが `POST /exec/{id}/start` を送信（`Connection: Upgrade, Upgrade: tcp`）
2. Dockerが `101 Switching Protocols` で応答
3. 接続が生TCPに切り替わり、multiplexed stream（8バイトヘッダ + ペイロード）でstdout/stderrを送信

nginxの `http` モジュールはこのプロトコル切り替えを完全には中継できない。`proxy_buffering off` や `proxy_set_header Upgrade` 等の設定を試したが、ストリームデータが中継されなかった。

nginx `stream` モジュール（L4プロキシ）を使えばTCPレベルの中継は可能だが、URLパスベースのルーティングができないため、既存のエンドポイントフィルタリングと共存できない。

## 決定

Go製の自作プロキシに置き換える。Goの `net/http` は `http.Hijacker` インターフェースを通じてHTTP接続のハイジャックをネイティブサポートしており、1つのHTTPサーバー上でL7とL4を混在できる。

## アーキテクチャ

```
AI Container ──(Unix Socket)──> Go Proxy ──(Docker Socket)──> Docker Engine
```

エンドポイントごとに処理レベルを使い分ける:

| エンドポイント | 処理レベル | 内容 |
|---|---|---|
| `containers/json` | L7 (HTTP) | コンテナ一覧を許可リストでフィルタリング |
| `containers/{id}/json` | L7 (HTTP) | コンテナ名/IDを許可リストで検証 |
| `containers/{id}/exec` | L7 (HTTP) | コンテナ検証 + JSONボディを解析しCmdを許可リストで検証 |
| `exec/{id}/start` | L4 (TCP) | HTTP hijackで生TCPパイプ |
| `_ping`, `version` 等 | L7 (HTTP) | httputil.ReverseProxy でパススルー |
| その他 | - | 403 Forbidden |

### コンテナアクセス制御

`DOCKER_PROXY_CONTAINERS` 環境変数（カンマ区切り）で、アクセスを許可するコンテナ名を指定する。未指定時は全コンテナへのアクセスを拒否（安全側デフォルト）。

- `containers/json`: レスポンスをフィルタし、許可コンテナのみ返す
- `containers/{id}/json`, `containers/{id}/exec`: URLパスからコンテナ名/IDを抽出し、許可リストにない場合は403
- コンテナ名は完全一致のほか、短縮ID（12文字以上のprefix）でもマッチする

### exec/start のストリーム中継

```go
// 1. リクエストボディを読んでからクライアント接続をhijack
// 2. Docker socketに接続し、HTTPリクエストを手動で書き込み
// 3. 双方向io.Copyで生バイトを中継（HTTPレスポンス解析なし）
// 4. half-close (CloseWrite) で片方向のEOFを伝播
```

ポイント:
- Dockerからのレスポンス（101ヘッダ + multiplexed stream）を**一切パースせず**そのままクライアントに流す
- `resp.Write()` を使うとchunked encodingで再エンコードされるため不可
- 片方向が終了したらfull closeではなく `CloseWrite()` で半閉にし、もう片方のストリームを維持する

## 構成

```
docker/docker-proxy/
  main.go          # プロキシ本体（~230行、外部依存なし）
  Dockerfile       # マルチステージビルド（golang:alpine → alpine）
  entrypoint.sh    # 許可リスト書き出し、Docker socket GID対応、非rootユーザーで起動
```

## 却下した代替案

| 案 | 却下理由 |
|---|---|
| nginx http + 設定調整 | HTTP hijackを根本的にサポートしない |
| nginx stream | URLベースルーティング不可、httpと同一ソケットで共存不可 |
| nginx http + stream 2ソケット構成 | Docker CLIからの使い分けが非現実的 |
| C# (ASP.NET Core) | 動作可能だがイメージサイズが大きい（AOTで15-20MB vs Go 10MB） |
