# 現在の制御・音声・運用

対象は今回のカメラ実機。古い `reference/` は初回セットアップの参照版であり、以下の後続変更とは分けている。

## IR切替

既存の稼働ディレクトリでは、保存済みの私用認証設定を読み取る。

```bash
cd /home/nmt3325/iermu_cam
python3 camctl.py night auto
python3 camctl.py night day
python3 camctl.py night night
python3 camctl.py status
```

`auto` は自動、`day` は昼、`night` は夜。終了時点では自動へ戻している。パラメータ41の24バイトを読み、閾値などを保ってオフセット4だけを変更する。元データは私用 `stab/` へ保存する。

夜モードで映像がモノクロになり、自動への復帰でカラーへ戻ることを確認した。暗室でのIR LED照射は未確認。**GET 41は現在の実動作モードを返さない**ため、応答値だけをもって切替確認とはしない。制御はランタイム操作で、起動時のモード固定は行っていない。

リポジトリの `client/camctl.py` を単独使用する場合は `CAMERA_USER` と `CAMERA_PASSWORD` を環境変数で指定する。既定の認証文字列は同梱しない。`CAMERA_IP` は任意で、既定値は `192.168.10.34`。`.env.example` は自動読込しない。

## 映像とマイクのライブ再生

通常の視聴は [Tailscale経由のRTMP視聴](TAILSCALE-RTMP.md) の中継URLを使う。以下の直接受信コマンドは、常時中継を一時停止して検証するときの手順。直接試験の前に `sudo systemctl stop iermu-rtmp-publish`、終了後に `sudo systemctl start iermu-rtmp-publish` を実行する。

```bash
cd /home/nmt3325/iermu_cam
./camera-stream 180 | ffplay -i -
```

リポジトリ側では `./client/camera-stream` を使用する。引数は受信秒数、未指定時は86400秒。ffplayは再生する端末へ別途用意する。ストリームは標準出力へ出し、映像・音声ファイルの保存を必須にしない。

H.264 1280×720、AAC-LC 11025Hz・モノラル。カメラ独自の映像NAL/AAC末尾を `fix_flv.py` で取り除き、ライブで復号できることを確認した。マイクは無音データだけではなく、非ゼロPCMを確認している。回線断後の自動再接続は、このCLI単体には未実装。常時中継はsystemdの再起動ポリシーで再接続する。

## スピーカーの残作業

アプリのネイティブ実装はAACをFLV化し、RTMPで送る。ローカル接続・ストリーム作成は成功するが、publish成功応答と実際のスピーカー再生は未確認。小音量・約1秒の確認音を一度送ったが、ソケットへの送信完了だけで再生成功とはしない。LAN2の1002という定数を単純に1バイト化したコマンドも対象機では受理されなかった。設定の無差別な書換えはしていない。

## 現行ネットワーク

- `wlp1s0`：専用SSID `iermu-cam`、APゲートウェイ `192.168.10.254`。カメラだけをMAC ACLで許可する。
- `iermu-sta0`：別のローカル管理MACで家庭用APへ接続。DHCPアドレスを使用し、家庭用ルーター `.1` は奪わない。
- カメラは `192.168.10.34`。専用APへのホスト経路と両インターフェースのproxy ARPで、家庭内サブネットからの到達を支える。
- 無線1台を同時利用するため、両接続はチャンネル11を共有する。家庭用APのチャンネル変更時は再設定が必要。
- デフォルト経路は有線 `ens18`。家庭用Wi-Fi接続ではデフォルト経路・DNSを上書きしない。Wi-Fi省電力を無効化する。
- `.36` のDIGAは、家庭用APへ直接接続した側からもARP応答がなかった。機器の稼働や現在IPを確認するまで、DIGA復旧済みとは扱わない。

## 自動起動と確認

```bash
systemctl is-enabled iermu-wifi-vif iermu-ap iermu-fakecloud iermu-sslcloud
systemctl is-active iermu-wifi-vif iermu-ap iermu-dhcp iermu-fakecloud iermu-sslcloud
ip -4 route get 192.168.10.34
nmcli -g GENERAL.STATE,IP4.ADDRESS device show iermu-sta0
```

永続systemdユニット、自動起動、再起動ポリシー、APのIP/iptables再構築、ログローテーションを設定した。ホスト再起動後の動作と、今回のSSID再設定後の物理電源再投入は未検証。09時台の電源再投入検証と混同しない。

`deployment/` は現行ファイルの参照と例示値入り設定。家庭用SSID/PSK、許可する実MAC、radio-private.json、NetworkManagerの家庭用接続プロファイル、TLS秘密鍵・証明書は同梱しない。例示値のまま自動配備するインストーラーではない。TLS受信の外部ライブラリは今回 `pylib/` に導入したtlslite-ng/ecdsa/sixを使用し、リポジトリにはベンダリングしていない。


## 最終の受信・補正検証

専用APで180秒の映像・音声を受信し、pingは180/180、損失0%。初回の長時間復号では4件のNAL type 0と7件の出力時刻丸め警告を確認した。長さだけでなくNALヘッダも検査する補正へ変更し、出力時刻は1ms基準・passthroughで再検証した。65秒受信の再検証では映像・音声の復号が完了し、警告は0件。短期の確認であり、長期無停止を保証する結果ではない。

オフライン検査は21件合格。値と確認範囲は [検証データ](../evidence/control-verification-20261111.json) に保存した。部屋の映像・録音、実SSID/PSK/MAC/トークンは収録していない。
