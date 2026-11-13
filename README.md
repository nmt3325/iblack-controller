# iermu camera local

古いiermuカメラから映像・マイク音声をローカルで取り出し、IRモードを制御し、Tailscale経由で視聴するためのコード・設定例・解析記録です。対象は今回の実機、作業記録は2026年11月10日〜13日（日本時間）。

## 今できること

| 機能 | 到達点 | 限界・未確認 |
| --- | --- | --- |
| 通常Atermへの接続・設定 | カメラ `192.168.10.35`、LinuxをDNSに指定。有線IPへ実通信を誘導 | カメラは固定IP。DHCP予約／除外、電源再投入と長期試験は別途 |
| 映像 | H.264、1280×720、独自FLV末尾を補正して再生 | 長期連続運転は未検証 |
| マイク | AAC-LC、11025Hz、モノラル。非ゼロPCMと復号を確認 | スピーカーへの送信とは別機能 |
| IR・昼夜切替 | 自動／昼／夜。モノクロ化とカラーへの復帰を確認 | 暗室でのIR LED照射、設定の起動時固定は未確認 |
| Tailscale視聴 | 別ノードから映像・音声を受信・復号 | RTMP対応プレイヤーとTailscale接続が必要 |
| 常駐・復旧設定 | systemd、自動起動、再起動ポリシー、ログローテーション | ホスト再起動後の実動作は未検証 |
| カメラDNS設定 | 実機NETSET・保存後再接続・実効DNSの有線到着を確認。アプリ順序を明示選択可能 | 対象実機限定。固定IP化を伴う。電源再投入後の保持は未試験 |
| クラウド受信先の変更 | 対象ドメインをLinux有線 `192.168.1.201` へ解決。HTTPS/3399/3368到着確認 | ネイティブRTMP自動プッシュは未成立。現行視聴はpull方式 |
| スピーカー | アプリのAAC→FLV→RTMP経路を解析 | publish成功・実音とも未確認。未完成 |
| ネットワークtelnet制御 | TCP3508の専用コマンドで起動・停止。2台目でログイン画面と停止→再有効化を実機確認 | OS認証・rootシェル・永続化・1台目の起動は未確認。手順は[こちら](docs/TELNET.md) |

### 通常の視聴URL

```text
rtmp://100.87.20.49:1935/live/camera
```

視聴端末をTailscaleへ接続し、VLCなどで開きます。中継はTailscale IPv4だけで待ち受け、映像・音声は再エンコードせず補正・再多重化します。ブラウザーでの直接再生や双方向通話を実装したものではありません。

## 読む順序

| 文書 | 内容 |
| --- | --- |
| [全進捗・確認範囲](docs/PROGRESS.md) | 時系列、できること、失敗した試行、未完了事項 |
| [使った手法](docs/METHODS.md) | パケット解析、アプリ静的解析、制御プロトコル、FLV補正 |
| [通常Aterm APへの移行](docs/HOME-AP.md) | 現在のIP・DNS・受信先・制限・復旧 |
| [有線側への自動送信調査](docs/NATIVE-PUSH.md) | SDKの送信先決定、Telnet認証、未切替の理由と移行条件 |
| [SDKトークン・配信先の解析](docs/SDK-UPLOAD.md) | INIのTracker、UDPの接続先応答、RTMP URL構成、23ケースのARM部分実行 |
| [ストリーム停止の修復](docs/STREAM-RECOVERY.md) | 物理STA・IP固定・直接経路ガード・最終実再生と限界 |
| [現行構成](docs/ARCHITECTURE.md) | AP/STA、経路、クラウド受信、中継 |
| [引継ぎ・依存関係](docs/HANDOFF.md) | 稼働場所、確認コマンド、再現範囲、注意点 |
| [アプリ全操作の調査](docs/APP-OPERATIONS.md) | 画面・条件・LAN/クラウド/端末処理・保存・未検証範囲 |
| [ファームウェア更新の調査](docs/FIRMWARE-UPDATE.md) | APK側のクラウド／NAS更新指示と、ダンプ解析前の記録 |
| [自作ファーム更新の実現性](docs/FIRMWARE-SELF-UPDATE.md) | SysUpdateのローカル更新、ARM命令による19ケース、appfs／bakfs同時上書きの注意。実機書込み・起動は未検証 |
| [IR・マイク・操作](docs/CONTROLS.md) | 操作コマンド、制約、実機確認の詳細 |
| [Tailscale RTMP](docs/TAILSCALE-RTMP.md) | 視聴、サービス、アクセス制限、外部ノード検証 |
| [カメラ自身のDNS設定](docs/DNS.md) | 操作・安全策・DHCPと実効DNSの注意点 |
| [制御プロトコル](docs/PROTOCOL.md) | TCP 3357、コマンド、本体配置、応答の解釈 |
| [検証根拠](docs/VERIFICATION.md) | 初回記録と後続の検証JSONへの案内 |
| [解析補助ツール](research/README.md) | APK/ELF解析の入力ハッシュと再現手順 |
| [ファームウェア原本・全展開物](firmware/iblack-8m/README.md) | 8MiBダンプ、展開ZIP、全200ファイル、追加解析と22テスト |
| [秘密情報の扱い](SECURITY.md) | Gitに入れていないもの |

## ディレクトリ

```text
client/       現行のCMS制御、RTMP取得、FLV補正、ストリーム出力
server/       SSLv3/TLS互換HTTP受信と依存ライブラリ一覧
deployment/  現行systemd・AP/STA・中継設定の参照版／例示値
research/     静的解析ツール、解析経路、未解決事項
firmware/     ファームウェア原本・全展開物・追加解析（LFS不要）
docs/        日本語の進捗・手法・構成・運用手順
 evidence/    識別子を除いた検証記録とSHA-256一覧
reference/    初回採取版。現行設定とは区別して保存
tests/       通信しないユニットテスト
tools/       構文、相対リンク、参照版ハッシュ、テストの検査
```

## 最新の復旧確認

Linux側の無線を単一の物理STAへ整理し、既存IPを固定して映像・音声を復旧しました。最終確認は別Tailscaleノードで60秒の実再生。無線切断そのものの根治や長期安定性は未検証です。[修復内容と制限](docs/STREAM-RECOVERY.md)を参照してください。

## 検証

```bash
python3 tools/verify_repo.py
```

カメラへの接続、Wi-Fi切替、iptables変更は行いません。現時点のユニットテストは103件（telnet関連14件を含む。Unicorn未導入ならそのうち4件をskip）（うちアプリ操作索引の合成Javaテスト10件）。DNSの33件を含め、テストは全てオフライン。実機では180秒の映像・音声受信とping 180/180、補正追加後65秒の警告なし復号、別のTailscaleノードで約12秒の映像・音声復号を確認しています。Aterm移行後は有線受信の合成映像26フレームと、Tailscaleの実映像86フレーム・音声を復号しました。クラウド向けのカメラ自動pushとは区別しています。これらは長期安定性の保証ではありません。

## 保存範囲

- 稼働機は `debian-02`。作業用Gitと `/home/nmt3325/iermu_cam` の実運用ディレクトリは分離しています。
- `deployment/` は既存環境の参照資料です。パス・IP・無線構成は機体依存で、汎用の自動インストーラーではありません。
- Wi-Fi PSK、家庭用SSID、実MAC、認証文字列、トークン、TLS秘密鍵、未加工の通信ログ、部屋の映像・音声、APK/Android由来の共有ライブラリ本体は同梱しません。ファームウェア由来のバイナリは `firmware/` に収録します。
- TCP/UDP受信と最小HTTP応答は用意していますが、メーカーのクラウドAPI全体を再実装したものではありません。
- ソースは下記GitHubリポジトリで共有します。運用機の私的設定や部屋のメディアは公開しません。

## 初回の記録

初回のAPアドレスは `.1`、現行は `.254` です。旧値を現行設定へ戻さないでください。

- [初回セットアップ](docs/SETUP.md)
- [初回の運用採取](docs/OPERATIONS.md)
- [初回のメディア確認](docs/MEDIA.md)
- [初回参照版の説明](reference/README.md)

## 追加調査

[TCP3356の映像・音声取得](docs/VIDEO3356.md)：3357認証、3356要求形式、映像・音声の別接続、現行RTMPとの違いと未確認範囲。通常中継はRTMPのまま。

[本体の追加調査](docs/CAMERA-DEEPER.md)：3356のPTS/DTS、画質変更の実装、スピーカー形式、UART候補、実再生チェックで検出した中継停止と復旧。

[アプリ操作の全体調査](docs/APP-OPERATIONS.md)：HTTP/LAN/Businessの契約宣言、画面の操作条件、夜間・露出の値、PTZのLAN/クラウド分岐、録画・NAS・警報・共有・設定、名前だけでは分からない副作用。全機能の実機対応を確認したという意味ではありません。

## 解析原本は別アーカイブ（LFS不要）

本体は通常のGitだけでclone・検証できる軽量構成。APK・DEX・Android由来SO・JADXのJava全文とリソース・JADX/JDK本体は[別保管の解析原本](artifacts/README.md)としてSeafileへ分離した。Gitには取得先とハッシュだけを残す。APK原本なしで110件のオフラインテストを実行できる。ARM命令の再現テストには対応ファームウェアと任意依存のUnicorn／pyelftoolsが必要で、未導入時は該当テストをスキップする。

```sh
python3 tools/verify_repo.py
# 原本も検証する場合のみ
python3 tools/verify_repo.py --artifacts-root /path/to/iermu-camera-analysis-artifacts-5ce4fb0
```

## ソースリポジトリ

GitHub: https://github.com/nmt3325/iblack-controller

APK由来の解析原本と大きなツールチェーンは `artifacts/archive.json` のSeafile保存先から別途取得する。ユーザー指定のファームウェア原本・展開ZIP・全展開物は [firmware/iblack-8m/](firmware/iblack-8m/README.md) に通常Gitで収録している。

公開前の履歴検査で、古い証跡に残っていた家庭用Wi-FiのSSID 1件を例示値へ置換した。変更内容・コミット順序は保持し、影響するSHAは再計算している。元のLFS入りリポジトリと置換前の履歴はローカルに保全し、公開しない。
