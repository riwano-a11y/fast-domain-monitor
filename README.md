# GitHub Fast Domain Monitor

CTログに現れた新しいドメインをDNS照合し、指定IPへ向いていたらSlackへ通知します。
VirusTotalを経由しません。

## 1. GitHubへアップロード

1. このZIPを展開します。
2. GitHubで新しい **Public repository** を作ります。
3. リポジトリの **Add file → Upload files** を開きます。
4. 展開後の `github-fast-domain-monitor` フォルダの**中身をすべて**アップロードします。
5. `Commit changes` を押します。

アップロード後、GitHub上で次の3つが見えれば正常です。

- `.github/workflows/monitor.yml`
- `monitor.py`
- `requirements.txt`

## 2. 監視IPをSecretへ登録

リポジトリで次の順に開きます。

`Settings → Secrets and variables → Actions → New repository secret`

Name:

```text
TARGET_IPS
```

Secret:

```text
18.179.211.152
```

複数IPを監視する場合はカンマ区切りです。

```text
18.179.211.152,203.0.113.10
```

## 3. Slack WebhookをSecretへ登録

もう一度 `New repository secret` を押します。

Name:

```text
SLACK_WEBHOOK_URL
```

Secret:

```text
https://hooks.slack.com/services/...
```

Webhook URLそのものをGitHubのファイルへ書かないでください。

## 4. 初回テスト

1. リポジトリ上部の `Actions` を開きます。
2. 左側の `Fast Domain Monitor` を選びます。
3. `Run workflow → Run workflow` を押します。
4. Slackへ `Fast Domain Monitor test succeeded` が届けば設定完了です。

以後は5分ごとに自動実行されます。対象IPと一致した場合、ドメイン、IP、検知時刻、
URLをSlackへ通知します。

## 重要

- GitHub Actionsのスケジュールは最短5分間隔で、混雑時は開始が遅れる場合があります。
- Public repositoryの標準GitHub-hosted runnerは無料です。Private repositoryでは実行時間枠を消費します。
- Public repositoryでもSecretsの値は公開されません。ただし、ファイルへ直接書いた値は公開されます。
- Public repositoryの定期実行は、リポジトリに60日間活動がないと自動停止する場合があります。
- CTログに証明書が出た時点ではDNSがまだ対象IPへ向いていないことがあります。その場合、この方式だけでは一致を検出できません。
- 公開CertStream側が停止すると、その回は接続エラーになります。

## CertStream接続先を変更する場合（任意）

通常は不要です。接続先を変更するときだけ、次のSecretを追加します。

Name:

```text
CERTSTREAM_URL
```

Secret:

```text
wss://your-certstream-server.example/domains-only
```

未登録時は標準接続先を使用します。
