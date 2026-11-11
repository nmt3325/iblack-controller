# 運用確認

> 現在は通常Aterm AP構成へ移行済み。カメラは `.10.35`、DNS／クラウド受信先はLinux有線 `.1.201`。専用APは停止し、現行の手順・確認範囲は [通常AP移行](HOME-AP.md) を優先する。以下の専用AP構成の記述は旧構成・救済用の参照。

> 初回採取時の記録。後続の実機変更・現在の操作は [現在の制御・音声・運用](CONTROLS.md) を参照。

## 読み取り中心の確認コマンド

```bash
date -Is
systemctl is-active iermu-ap.service iermu-dhcp.service iermu-fakecloud.service
iw dev wlp1s0 info
sudo hostapd_cli -p /run/hostapd -i wlp1s0 all_sta
ping -I wlp1s0 -c 5 -W 1 192.168.10.34
ip neigh show 192.168.10.34 dev wlp1s0
sudo journalctl -u iermu-ap.service --since '10 min ago'
sudo journalctl -u iermu-dhcp.service --since '10 min ago'
sudo iptables -t nat -L IERMU_PRE -n -v
```

APの接続確認では、対象MACの `AUTHORIZED` と `connected_time` を確認します。pingや近隣テーブルのMAC照合を合わせると、予約IPを使用している機体の疎通も確認できます。

## 実機のファイル

| パス | 内容 |
| --- | --- |
| `/home/nmt3325/iermu_cam/fakecloud.py` | ローカル受信プログラム |
| `/home/nmt3325/iermu_cam/fakecloud.log` | TCP/UDP受信ログ |
| `/home/nmt3325/iermu_cam/ap/iermu_reset_capture.sh` | 1回限りのセットアップ待受・AP復旧 |
| `/home/nmt3325/iermu_cam/ap/reset_capture_*.log` | セットアップ実行記録 |
| `/run/iermu-reset-capture/status` | 最後のセットアップ処理状態 |
| `/etc/hostapd/iermu-ap.conf` | AP設定 |
| `/etc/hostapd/iermu-ap.accept` | 許可MAC |
| `/etc/iermu-ap/dnsmasq.conf` | DHCP/DNS設定 |
| `/var/lib/misc/iermu-ap.leases` | DHCPリース |
| `/usr/local/sbin/iermu-ap-up` | AP側アドレス・転送・MASQUERADE |
| `/usr/local/sbin/iermu-ap-down` | 上記の解除 |
| `/usr/local/sbin/iermu-ap-status` | AP状態確認用補助スクリプト |

## サービスの状態

ソース取得時の確認結果です。

| サービス | 実行状態 | 自動起動の表示 |
| --- | --- | --- |
| `iermu-ap.service` | active | disabled |
| `iermu-dhcp.service` | active | static |
| `iermu-fakecloud.service` | active | transient |

`iermu-ap.service` は `Wants=iermu-dhcp.service` でDHCPを要求します。DHCP側は `After=iermu-ap.service` と `PartOf=iermu-ap.service` を持ちます。

ローカル受信プログラムは、次の形式の一時サービスとして起動されています。

```bash
sudo systemd-run --unit=iermu-fakecloud --collect \
  /usr/bin/python3 /home/nmt3325/iermu_cam/fakecloud.py
```

上記は、同名サービスが存在しない状態での起動形式です。実行中に重ねて起動するためのコマンドではありません。

確認時点で `iermu-ap-autorevert.timer` はinactiveです。今回のセットアップ待受は、自動でホームWi-Fiへ戻すタイマーを作成しません。

## 記録の読み方

- `AP-STA-CONNECTED` と `DHCPACK` は、無線接続とアドレス取得の根拠です。
- DNSの `config ... is 192.168.10.1` は、ローカルへの名前解決の根拠です。
- `fakecloud.log` のTCP OPEN/DATAやUDP受信は、ローカル受信の根拠です。
- これらはHTTP/TLSの認証成功やクラウドAPI処理完了を表すものではありません。
