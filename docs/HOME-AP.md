# 通常のAterm APで動かす現行構成

> 2026-11-12 修復後の現行無線は物理STA `wlp1s0`、固定IP `192.168.10.22`。以下の初回移行記録には旧仮想STAの記載が残るため、現行構成・制限は[ストリーム修復](STREAM-RECOVERY.md)を優先してください。

## 到達点

専用APへの接続は不要になった。カメラは実際のAtermへ接続し、カメラ自身のDNSをLinuxへ変更した。DNS問い合わせ・HTTPS・TCP 3399・UDP 3368がLinuxの有線インターフェースへ到着することをパケットヘッダーで確認した。Wi-Fi認証情報の再書込みは不要で、既存のAterm用第1プロファイルを保持した。

| 項目 | 現在値 |
| --- | --- |
| カメラ | `192.168.10.35/24`、静的設定 |
| カメラのゲートウェイ | Aterm `192.168.10.1` |
| カメラのDNS | 主 `192.168.1.201`、副 `192.168.10.22`。両方Linux |
| Linux有線 | `ens18` = `192.168.1.201` |
| LinuxのAtermクライアント | `iermu-sta0` = `192.168.10.22` |
| Atermの有線側NAT送信元 | `192.168.1.3` |
| 専用AP／専用DHCP | 停止・自動起動無効。設定は救済用に保持 |
| 視聴 | `rtmp://100.87.20.49:1935/live/camera` |

**`192.168.10.35`は以前のDHCPリースと同じ値を固定した。Aterm側でDHCP予約または配布対象外の設定を行い、将来のアドレス重複を避けること。** Linuxの両IPとAterm WAN側IPも変わると設定の追従が必要。ルーターのDHCP/DNS設定やLinuxのシステムDNSは変更していない。家庭用LANでLinuxを新たなDHCPサーバーにもしていない。

## 通信の分離

```text
カメラ .10.35 --Wi-Fi--> Aterm .10.1 --NAT (.1.3)--> Linux有線 .1.201
                                                DNS / HTTPS / cloud control / RTMP受信口

Linux STA .10.22 --RTMP pull--> カメラ .10.35
Linux --FLV補正・再多重化--> Tailscale 100.87.20.49:1935/live/camera
```

カメラがLinux有線へ接続できることは、Aterm経由のTCP SYN/SYN-ACKと、移行後の実DNS・HTTPS通信の両方で確認した。Linux自身の別インターフェースを宛先にした通常の接続テストはローカル経路の影響を受けるため、到達不可の根拠にはしなかった。

## DNSの上書き範囲

`iermu.com`、`dvrnat.com`、`yunwt.com`、`c.pcs.baidu.com` 配下のIPv4を `192.168.1.201` にする。`baidu.com` 全体を上書きしない。AAAAが上流へ漏れないよう対象ドメインをlocal扱いにもした。NTPの `cn.ntp.org.cn` と `time.nist.gov` は通常の上流DNSで解決する。

75秒の実通信観測では、Aterm NAT送信元からLinux有線へ49回のDNS問い合わせがあり、`api.iermu.com`、`www.iermu.com`、`www.dvrnat.com` が有線IPへ解決された。`yunwt.com` と `c.pcs.baidu.com` は設定・手動照会では確認したが、その観測窓でカメラ自身が問い合わせたとは主張しない。

## カメラ自身の設定手順

44バイトNETSETの既知フィールドだけを変更し、未知の末尾は実機からの最新値を保持した。DHCP有効のままDNSだけ書いた最初の試行は応答が成功でも読出しが旧値で、保存しなかった。次にAPKの手順を確認し、DHCPフラグ3を0、DNSを16〜23で指定し、`75/8` → 成功応答 → `65/3` とした。接続が一時中断した後、44バイト全体の完全一致と実効DNSを確認した。

この順序を明示選択できるよう `camctl.py` を拡張した。既定の読出し先行の安全な経路は残し、保存先行は自動的に選択しない。

```bash
cd /home/nmt3325/iermu_cam
# 既定ではプレビューだけ。現在値なら何も書かない
python3 camctl.py dns-set 192.168.1.201 --host 192.168.10.35 \
  --secondary 192.168.10.22 --app-save-sequence --static

# 変更が本当に必要なときだけ、プレビューを確認して明示的に適用・保存
python3 camctl.py dns-set 192.168.1.201 --host 192.168.10.35 \
  --secondary 192.168.10.22 --app-save-sequence --static \
  --apply --save --experimental
```

`--static` は現在のIP・マスク・ゲートウェイを変えずDHCPだけ無効化する。`--app-save-sequence` は保存前の読出し一致を必須にしない代わりに、保存後の再接続で全44バイトの一致が確認できなければ未確認として失敗させる。再送は読出し接続のみで、設定送信を自動反復しない。CLI自身はパケット検証や電源再投入を行わない。

## 常駐サービスと制限

- `iermu-home-network`: DNS/RTMPの送信元制限とHTTPS 443→8443転送。既存の他のiptablesチェーンは全消去しない。
- `iermu-home-dns`: 有線IPとSTA IPでDNS待受け。家庭LANへのDHCP配布はなし。
- `iermu-cloud-rtmp`: 有線IPの1935番で独立したRTMP受信。不要なRTSP/HLS/WebRTC/SRT/MoQ/API等は無効。
- `iermu-fakecloud` / `iermu-sslcloud`: 3399/TCP、3368/UDP、旧SSLv3互換HTTPS。HTTP応答はまだ完全なメーカーAPIではない。
- `iermu-rtmp-publish`: 取得先を `.10.35` に変更。専用APへのRequires/PartOf依存を外した。
- `iermu-wifi-vif`: Aterm用STAを起動。専用APが停止していても必要。

DNS・有線RTMPの新規受信は、現在のAterm NAT送信元・カメラ・Linux自身に範囲を限定した。NAT送信元が `.1.3` から変わったら `iermu-home-network` とcloud MediaMTX許可IPも更新する。

## 映像の確認と未完成部分

- 有線RTMP受信は**合成テスト映像・音声**で確認。H.264 320×180とAAC 11025Hz、26フレームを復号した。これはカメラからのクラウドプッシュの検証ではない。
- カメラを排他的に取得し、720p H.264とマイクAAC、97フレームを復号した。短い復号テストの終了で下流が閉じたためのBroken pipeは、ネットワーク断とは区別した。
- Aterm移行後のTailscale待受けURLから、720pとマイク音声、86フレームをエラーなしで復号した。今回の再確認はLinux自身からであり、別Tailscaleノードは移行後には再試験していない。
- カメラへの同時RTMP取得ではタイムアウトが出たため、常駐取得は1本に限定し、利用端末は中継を見る。
- HTTPSの `GET /v2/pcs/device` はLinuxで復号して受信できた。しかしメーカーの登録/API応答は未実装で、**カメラ自身からのクラウド向けRTMP自動プッシュは未成立・未確認**。現在の視聴はpull方式のまま。
- 物理電源再投入、Linux再起動、長期連続運転は今回未試験。自動起動を有効にしたことと、起動試験済みは区別する。

## 確認・復旧

```bash
systemctl is-active iermu-home-network iermu-home-dns iermu-cloud-rtmp \
  iermu-wifi-vif iermu-fakecloud iermu-sslcloud iermu-rtmp iermu-rtmp-publish
python3 camctl.py dns --host 192.168.10.35
dig @192.168.1.201 api.iermu.com A +short
```

専用APを救済用に戻す場合は `sudo systemctl start iermu-ap`。通常運用で再び有効化する必要はない。カメラの元NETSET・Wi-Fi・ホスト設定のバックアップは稼働機の私用 `stab/` に0600で保存し、Gitには含めない。DNSだけの復元は [DNS操作](DNS.md) の `dns-restore` を使えるが、DHCPやIPまで戻す操作ではない。

根拠：[通常AP移行の検証JSON](../evidence/home-ap-migration-20261111.json)。
