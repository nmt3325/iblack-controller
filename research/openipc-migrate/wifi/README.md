# 2台目のWi-Fi（ホストのAPから自宅のルーターへ、OpenIPC）

結果は [../results/unit2-home-wifi.json](../results/unit2-home-wifi.json)。カメラ上の変更は、envの `wlanssid` と `wlanpass` の2つだけ（`fw_setenv`）。ファイルは変えていない。U-Bootもfirmwareも書いていない。

## 状態（2026-11-16）

- 2台目は、ホストのAP（Linuxホストの無線LANで立てた専用のAP）ではなく、自宅のルーター（Aterm）の2.4 GHz（チャネル11）に直接つながる。再起動しても同じところにつながる。
- アドレスはルーターのDHCPから受ける（リースは24時間）。ルーターの設定は変えていない。
- 時計がNTP（`pool.ntp.org`）で合うようになった。ホストのAPにいた間はNTPが届かず、時計は約2.4日遅れていた。
- ホストは、無線LAN（`wlp1s0`）をAPとして使うのをやめ、ルーターの2.4 GHzに子機としてつないだ。ホストの有線LANはルーターのWi-Fiとは別のサブネットにあり、有線からはカメラが見えないため。
- 映像（RTSP、MJPEG、静止画）、パスワードの確認、暗視の自動切り替えは、つなぎ替える前と同じように動く。

## 仕組み

OpenIPCの `/etc/network/interfaces.d/wlan0`（変えていない）:

```
iface wlan0 inet dhcp
    pre-up wlan_conf "$(fw_printenv -n wlanssid)" "$(fw_printenv -n wlanpass)" > /tmp/wpa_supplicant.conf
    pre-up wpa_supplicant -B -i wlan0 -D nl80211,wext -c /tmp/wpa_supplicant.conf
    post-down killall -q wpa_supplicant
```

起動のたびに、envの `wlanssid` と `wlanpass` から `wpa_supplicant` の設定を作り、DHCP（udhcpc）でアドレスを受ける。つなぎ先を変えるには、この2つを `fw_setenv` で書き換えて再起動すればよい。envを書き換えても、U-Boot本体には触れない。

## 手順（2026-11-16に行ったもの）

まずフラッシュに書かずに試し、つながることを確かめてからenvを書き換えた。

1. 準備: カメラのSSHのホスト鍵（ed25519の公開鍵）を控える。ルーターのSSIDとパスフレーズは2行のファイルにしてリポジトリの外に置き、コマンドラインには書かない（SSHの標準入力で渡す）。
2. 試験（[wifi-switch-trial.sh](wifi-switch-trial.sh)）: カメラの `/tmp` に試験用の `wpa_supplicant` の設定を作り、wlan0だけをルーターにつなぎ替える。300秒以内に `/tmp/wifi-trial.keep` ができなければ、envのネットワーク（ホストのAP）に戻る。戻った先のゲートウェイに届かなければ再起動する（envは元のままなので、ホストのAPにつながる）。ログにはSSIDもパスフレーズも書かない。
3. 発見: ホストの無線LANをAPからルーターの子機に切り替え、[find-by-hostkey.sh](find-by-hostkey.sh) で、同じネットワークにいる機器のSSHのホスト鍵を順に調べてカメラを見つける。rootのパスワードを別の機器に送らないため。見つけたら `/tmp/wifi-trial.keep` を作る（試験の開始から125秒で作った）。
4. 確定: envの `wlanssid` と `wlanpass` を書き換え、読み戻して、試験と同じ `wpa_supplicant` の設定ができることを確かめてから再起動する。

```sh
# 1. ホスト鍵を控える（wifi.txt は1行目がSSID、2行目がパスフレーズ）
ssh root@<カメラ> 'dropbearkey -y -f /etc/dropbear/dropbear_ed25519_host_key' | awk '/^ssh-ed25519/ {print $1, $2}' > hostkey.txt
# 2. 試験を始める（SSHを切っても続く）
ssh root@<カメラ> 'cat > /tmp/wifi-switch-trial.sh' < wifi-switch-trial.sh
ssh root@<カメラ> 'read -r s; read -r p; umask 077; wlan_conf "$s" "$p" > /tmp/wpa_trial.conf' < wifi.txt
ssh root@<カメラ> '( trap "" HUP; exec sh /tmp/wifi-switch-trial.sh 300 ) </dev/null >/dev/null 2>&1 &'
# 3. ホストをルーターにつなぎ替えて、カメラを探す
sudo nmcli connection down openipc-final-host-ap
sudo nmcli connection up iermu-home-uplink ifname wlp1s0
./find-by-hostkey.sh wlp1s0 hostkey.txt camera-address.txt
ssh root@$(cat camera-address.txt) 'touch /tmp/wifi-trial.keep; cat /tmp/wifi-trial.log'
# 4. 確定して再起動する
ssh root@$(cat camera-address.txt) 'read -r s; read -r p; fw_setenv wlanssid "$s" && fw_setenv wlanpass "$p"' < wifi.txt
ssh root@$(cat camera-address.txt) 'wlan_conf "$(fw_printenv -n wlanssid)" "$(fw_printenv -n wlanpass)" > /tmp/wpa_check.conf && cmp /tmp/wpa_check.conf /tmp/wpa_trial.conf && echo same'
ssh root@$(cat camera-address.txt) reboot
```

## 実機での確認（2026-11-16）

| 項目 | 結果 |
|---|---|
| 試験でのつなぎ替え | 試験の開始から約12秒でアドレスを受けた（スクリプト自身の待ちを含む）。リースは24時間 |
| 電波 | 2.462 GHz（チャネル11）、65 Mb/s、-49 dBm。再起動後は-50 dBm |
| ゲートウェイ | pingが通る |
| 時計 | 試験でつながった直後に、NTPで208,451秒（約2.4日）進んだ。再起動後も、ホストとの差は1秒以内 |
| 発見 | 有線LANからは見つからない。ホストの無線LANをルーターにつないでから、ホスト鍵で見つけた |
| 再起動 | 指示から47秒でpingが通った（カメラの起動から30秒）。アドレスもつなぎ先も同じ |
| 映像 | RTSP（`stream=0`）のDESCRIBEは200。MJPEGは8秒で34フレーム（1,048,673バイト）。`/image.jpg` は30,758バイトのJPEG。起動直後は今までどおり、`S96vicheck` がmajesticを1回起動し直してから出た |
| パスワード | RTSP、`/image.jpg`、`/mjpeg`、`/api/v1/config.json`、`/cgi-bin/status.cgi` は、パスワードなしでも、誤ったパスワードでも401。`/metrics` だけはパスワードなしで読める（今までと同じ） |
| 暗視 | 18時台に見て、夜（`night_enabled=1`、`night_mode_source=3`（ADC）、センサーの値14） |
| 外への通信 | 外へのTCP接続は見当たらなかった（再起動の13分後に4回見た） |

オフライン試験: [`tests/test_wifi_switch_trial.py`](../tests/test_wifi_switch_trial.py)。ホストのBusyBox ashで、ネットワークのコマンドを模擬したものに対して6つの場面を流す（残す、戻る、新しい側でアドレスが来ない、戻った先に届かず再起動、設定がない、ログにパスフレーズが出ない）。

## ホスト側

- `openipc-final-host-ap`（ホストのAP）は止めた。プロファイルは残してあり、自動では立たない。
- 無線LANは `iermu-home-uplink`（ルーターの2.4 GHz、固定のIPv4、既定の経路にはしない）でつなぐ。`iermu-home-uplink` と `iermu-home-uplink-5g` の自動接続は、APを使う前と同じ（有効）に戻した。
- 有線LANと既定の経路は変えていない。

## 元に戻す（ホストのAPへ）

ホストのAPのSSIDとパスフレーズ（リポジトリの外に控えてある）を、2行のファイル（`hostap.txt`）にしておく。

```sh
# カメラがルーターにつながっている間に、envを戻して再起動する
ssh root@<カメラ> 'read -r s; read -r p; fw_setenv wlanssid "$s" && fw_setenv wlanpass "$p"' < hostap.txt
ssh root@<カメラ> reboot
# すぐにホストの無線LANをAPに戻す（ルーターへの子機の接続は切れる）
sudo nmcli connection up openipc-final-host-ap
```

再起動の指示からカメラがWi-Fiを探し始めるまで30秒ほどあるので、その間にAPを立てる。envを書かずに試すだけなら、`wifi-switch-trial.sh` を逆向きに使う（`/tmp/wpa_trial.conf` をホストのAPの値で作る）。

## ルーターが止まったとき（2026-11-17）

ルーターが止まっても、カメラの側では何もしなくてよい。wpa_supplicantが同じSSIDを探し続け、udhcpcは `-b` で裏に回ってアドレスを求め続けるので、ルーターが戻れば自分でつながる。結果は [../results/unit2-router-outage.json](../results/unit2-router-outage.json)。

試験: [wifi-switch-trial.sh](wifi-switch-trial.sh)（`WAIT=540`）で、wlan0のつなぎ先を、まだ立てていないホストのAPに一時的に向けた（止まったルーターの代わり）。envは書いていない。

| 項目 | 結果 |
|---|---|
| APがない間 | 181秒、どこにもつながらない。再起動はしない |
| APを立てたあと | 11秒でアドレスを受け、pingが通った。SSHのホスト鍵は同じ |
| つながっている間にAPを60秒止める | APが戻ってから4秒で応答した |
| 540秒後 | 自分でenvのネットワーク（ルーター）に戻った。待ちが終わってから28秒でアドレスを受け、ゲートウェイに届いた |
| 再起動 | なし（uptimeは続いていた）。majesticは動いたまま |

ルーターが止まっている間にカメラを起動した場合は試していない。同じwpa_supplicantとudhcpc（`-b`）なので、ルーターが戻ればつながる見込み。

リセットボタン: 背面のリセットボタンを3〜30秒押して離すと、カメラが復旧用のAP（`OpenIPC-XXXX`）になり、そこから新しいSSIDとパスフレーズを入れられる（[ap/README.md](ap/README.md)）。純正のファームウェアでも、アプリ（`cmsMain.out`）がボタンを見て、AP用のドライバ（`mt7601Uap.ko`）に切り替えていた（ログの文字列 `3518 keydown time(%d),do cmsChangeWifiDevice2AP`）。OpenIPCに移すとそのアプリはなくなり、純正のAP用ドライバもOpenIPCのkernelでは読み込めないので、ボタンの見張りと、OpenIPCのドライバのソースから作ったAP用のドライバを `easy/post_setup.py`（版4）で入れる。

## 注意

- MT7601は2.4 GHzだけ。ルーターの2.4 GHzを止めたり、SSIDやパスフレーズを変えたりすると、カメラはつながらなくなる。そのときは、リセットボタンで復旧用のAPにして、`wifi-ap join` で新しい値を入れる（[ap/README.md](ap/README.md)）。ボタンが使えないときは、カメラのenvと同じSSIDとパスフレーズのAPを一時的に（たとえばホストで）立てるとカメラがそこにつながるので、新しい値を `fw_setenv` で入れて再起動する。
- アドレスはDHCPで決まる。変わると困るなら、ルーターの側でこのカメラにアドレスを固定する（DHCPの固定割当）。変わったときは、ルーターのネットワークにいるホストから `find-by-hostkey.sh` で探せる。
- カメラからインターネットに出られるようになった（NTPが届くのはこのため）。ルーターでポートは開けていない。`/metrics` は、自宅のネットワークにいればだれでもパスワードなしで読める。
- 再起動のとき、kernelのログに `NETDEV WATCHDOG: eth0 (hieth): transmit queue 0 timed out` が出た。この基板にEthernetはなく、`eth0` は使っていない。Wi-Fiへの影響は見えないが、調べていない。
- ホストの無線LANをルーターから離している間に、ホストの固定のIPv4アドレスを別の機器が使い始めることがある（2026-11-17に起きた。ルーターのDHCPが配ったとみられる）。NetworkManagerはアドレスの重複を見つけると、その接続をやめる。そのときは、保存しないDHCPの接続（`/run` に置かれる）で一時的につなぐ。ホストの固定アドレスは、ルーターのDHCPの範囲の外にするか、ルーターで固定割当にするとよい。
- SSID、パスフレーズ、アドレス、MACアドレス、ホスト鍵はGitに入れていない。

## 残っていること

- 長い時間（夜通しなど）つながり続けるかは、まだ見ていない。
- ルーターでアドレスを固定するかどうかは、所有者が決める。
- リセットボタンを実際に押して、復旧用のAPになることを確かめる。ボタンのGPIOは純正のファームウェアから読み、押したときと同じ処理と、値を書き換えたGPIOでの判定は確かめた（[ap/README.md](ap/README.md)）。
