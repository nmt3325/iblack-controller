# 現行構成

初回の `.1` を使う構成から変更している。以下が最終の実装で、初回採取版は `reference/` と[初回の運用記録](OPERATIONS.md)に残す。

## ネットワークとメディア

```text
カメラ 192.168.10.34
    │ 専用SSID iermu-cam / 2.4GHz / channel 11
    ▼
debian-02
    ├─ wlp1s0: AP 192.168.10.254/24
    │    ├─ hostapd: WPA2 + カメラのMAC ACL
    │    ├─ dnsmasq: DHCP予約・対象ドメインのDNS応答
    │    ├─ カメラへの /32 経路
    │    └─ カメラ限定のDNS/TCP/UDP転送
    ├─ iermu-sta0: 家庭用APへのmanaged接続、DHCP
    │    └─ APとは別MAC、同一channel、proxy ARP等
    ├─ ens18: 有線上流、通常のデフォルト経路
    ├─ TCP 3399 / UDP 3368: fakecloud受信・記録
    ├─ TCP 8443: SSLv3/TLS互換HTTP受信（カメラ443を転送）
    ├─ Python RTMP取得 → FLV補正 → FFmpeg copy
    └─ tailscale0 / 100.87.20.49:1935
         └─ MediaMTX / live/camera → Tailscale上の視聴端末
```

| 対象 | 値・意味 |
| --- | --- |
| カメラ | `192.168.10.34` |
| APゲートウェイ | `192.168.10.254` |
| 家庭用ルーター | `192.168.10.1`、AP側で奪わない |
| 制御 | TCP 3357 |
| カメラRTMP | `rtmp://192.168.10.34:1935/live/live` |
| 通常の視聴 | `rtmp://100.87.20.49:1935/live/camera` |
| ソースの映像 | H.264 1280×720 |
| ソースの音声 | AAC-LC 11025Hz・mono |

`192.168.10.0/24` を両無線インターフェースで扱うため、カメラのホスト経路、proxy ARP、ARP応答、rp_filterの設定を明示している。通常のブリッジや完全分離した別サブネットとは異なる。無線1台のAP/STA同時運用はチャンネル共有が前提。

## サービス

| ユニット | 役割 |
| --- | --- |
| `iermu-wifi-vif` | 家庭用STA仮想インターフェースと別MAC |
| `iermu-ap` | APとIP・経路・iptables |
| `iermu-dhcp` | DHCP/DNS。APから要求されるstaticユニット |
| `iermu-fakecloud` | TCP/UDP受信・記録 |
| `iermu-sslcloud` | SSLv3/TLS互換HTTP受信 |
| `iermu-rtmp` | Tailscale IPv4限定のMediaMTX |
| `iermu-rtmp-publish` | カメラ取得・補正・再多重化・publish |

publisherはAPとMediaMTXを必要とし、その再起動へ追従する。失敗・受信終了時は再起動する。現行ユニットは永続化し自動起動を設定したが、ホスト再起動後の実動作は未検証。

## アクセス境界

- カメラ用WPA2とMAC ACL。実PSK・MACはGitに入れない。
- クラウド通信誘導はカメラを対象にしたルール。家庭全体のDNSを置き換えない。
- MediaMTXは `100.87.20.49:1935` だけで待受。RTSP/HLS/WebRTC/API等は無効。
- `live/camera` のpublishは同ホスト、readはTailscale IPv4範囲。TailscaleのACLで許可された端末が対象。
- 生ログは資格情報や識別子を含み得る。共有時は要約または識別子を除いた記録を使う。

## 旧構成との相違

初回はAP `.1`、受信処理はtransient、AP自動起動はdisabledだった。途中の同名仮APも現行構成では廃止している。旧スナップショットをそのまま本番へ戻さないこと。
