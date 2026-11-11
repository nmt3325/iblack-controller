# セットアップ手順

> 現在は通常Aterm AP構成へ移行済み。カメラは `.10.35`、DNS／クラウド受信先はLinux有線 `.1.201`。専用APは停止し、現行の手順・確認範囲は [通常AP移行](HOME-AP.md) を優先する。以下の専用AP構成の記述は旧構成・救済用の参照。

> 初回採取時の記録。後続の実機変更・現在の操作は [現在の制御・音声・運用](CONTROLS.md) を参照。

ここでは、2026-11-11に接続と電源再投入後の再接続を確認した経路を記録します。既存実機の設定を対象とし、リポジトリの例示値をそのまま使用する手順ではありません。

## 前提

- debian-02を有線 `ens18` 経由で操作できる。
- `wlp1s0` をカメラ用APとセットアップ用クライアント接続に使用する。
- 実機のhostapd設定に、接続先SSID `iermu-cam` と正しいPSKが登録されている。
- カメラのセットアップ用SSID、PSK、制御ログイン情報、対象MACをローカル実設定で管理する。
- `/home/nmt3325/iermu_cam/iermu_run.sh` と `ap/iermu_reset_capture.sh` が実機に存在する。

## 1. 前提検査

```bash
ip route get 1.1.1.1
sudo bash /home/nmt3325/iermu_cam/ap/iermu_reset_capture.sh --check
```

前提検査では、元の通信処理を抽出できることと、その構文を確認します。`--check` はネットワーク切替やカメラへの書き込みを行いません。

## 2. 待受を開始

同名の処理が実行中でないことを確認してから起動します。

```bash
systemctl is-active iermu-reset-capture.service
sudo systemd-run \
  --unit=iermu-reset-capture \
  --property=RuntimeMaxSec=35min \
  --property=TimeoutStopSec=45s \
  /bin/bash /home/nmt3325/iermu_cam/ap/iermu_reset_capture.sh
```

スクリプトは専用APを停止し、セットアップ用Wi-Fiを最大30分待ち受けます。処理の重複をロックで防ぎ、終了時にはAPの復旧処理を行います。通常の監視や、常時実行するサービスではありません。

## 3. カメラをWireless Access Pointモードにする

本体の操作でセットアップ用Wi-Fiを出します。ボタンを押す時間や操作方法は機種依存のため、この記録では規定しません。

待受スクリプトが実施する処理は次のとおりです。

1. 既知のセットアップ用SSIDを検出して接続する。
2. ホストへ `192.168.58.251/24` を設定し、`192.168.58.1:3357` へ接続する。
3. バージョン・ログイン・機体情報を取得し、対象MACを照合する。
4. hostapd設定から取得したSSID/PSKをカメラへ送る。
5. `iermu-cam` のAP、DHCP/DNS、ローカル受信、対象カメラ用の通信転送を復旧する。
6. 対象MACのAP認証完了を確認する。

## 4. 接続を確認

```bash
sudo cat /run/iermu-reset-capture/status
sudo hostapd_cli -p /run/hostapd -i wlp1s0 sta '<実MAC>'
ping -I wlp1s0 -c 5 -W 1 192.168.10.34
sudo journalctl -u iermu-ap.service -u iermu-dhcp.service --since '10 min ago'
```

スクリプトの `JOINED_PERSISTENCE_UNTESTED` は、その時点のAP接続を確認した状態です。設定保持の確認は次の手順で別途行います。

## 5. 電源再投入後の設定保持を確認

- AP・DHCP/DNS・受信プログラムを稼働させたままにする。
- 設定用スクリプトを再実行しない。
- リセット操作ではなく、カメラの電源を入れ直す。
- 同じSSIDへ再接続し、予約IPを取得し、pingへ応答することを確認する。

今回の確認結果は、09:11:50の再接続、同時刻のDHCPACK、09:12:14のping 5/5です。カメラの設定を追加で書き直していない状態で確認しています。
