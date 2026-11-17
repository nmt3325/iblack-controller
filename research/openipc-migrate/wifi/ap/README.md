# 復旧用のAP（背面のリセットボタン、OpenIPC）

カメラが自宅のWi-Fiにつながらなくなったとき（ルーターの交換、SSIDやパスフレーズの変更など）に、背面のリセットボタンでカメラ自身をAPにして、新しいSSIDとパスフレーズを入れ直すための仕組み。純正のファームウェアにあった「ボタンの長押しでAPモード」の代わり。結果は [../../results/unit2-wifi-ap.json](../../results/unit2-wifi-ap.json)。

純正では、長押しのたびにスマートコンフィグとAPモードが切り替わった。OpenIPCにはスマートコンフィグがないので、ふだんの状態（Wi-Fiの子機）とAPを切り替える。

## 使い方

1. 背面のリセットボタンを3〜30秒押してから離す（3秒未満と30秒を超えたものは無視する）。
2. 10秒ほどで、カメラが `OpenIPC-XXXX`（XXXXはWi-FiのMACアドレスの末尾4桁）というAPになる。WPA2で、パスフレーズはそれまでのWi-Fiのパスフレーズ（envの `wlanpass`）。チャネル6。
3. パソコンやスマートフォンをそのAPにつなぐと、DHCPで `192.168.4.100` からのアドレスを受ける。カメラは `192.168.4.1`。Web画面（`http://192.168.4.1/`）もSSHも使える。
4. SSHで新しいネットワークを入れる:

```
ssh root@192.168.4.1
wifi-ap join "新しいSSID" "新しいパスフレーズ"     # 暗号なしのネットワークならパスフレーズを省く
```

`join` は、envの `wlanssid` と `wlanpass` に保存して（`fw_setenv`）、すぐそのネットワークにつなぐ。APの接続はそこで切れる。1分以内にアドレスを受けられなければAPに戻るので、入れ間違えたときはもう一度APにつないでやり直す。

APをやめて、保存してあるネットワークに戻るのは、次のどれか。

- もう一度ボタンを3〜30秒押して離す
- `wifi-ap stop`
- 600秒（`wlanap_idle`）、APにだれも通信しない
- 再起動する（再起動の後は、かならずWi-Fiの子機として起動する）

| コマンド | 内容 |
| --- | --- |
| `wifi-ap status` | 今のモード、APの設定（パスフレーズは表示しない）、ボタンの見張り |
| `wifi-ap start`、`stop`、`toggle` | ボタンを使わずにAPにする、戻す、切り替える（wlan0経由のSSHは切れる） |
| `wifi-ap join SSID [パスフレーズ]` | 新しいネットワークを保存して、つなぐ |
| `wifi-ap probe [秒]` | GPIOの入力の変化を表示する（ボタンのGPIOを調べる。既定20秒） |
| `logread \| grep wifi-ap` | 経過 |

設定（どれも任意。`fw_setenv 名前 値`）:

| 変数 | 内容 |
| --- | --- |
| `wlanap_ssid` | APのSSID（1〜32バイト。既定 `OpenIPC-XXXX`） |
| `wlanap_pass` | APのパスフレーズ（8〜63文字）。`none` で暗号なし。既定は `wlanpass`。`wifi-ap join` は、それまでAPが使っていたパスフレーズをここに保存する（APにつなぐ側が覚えているものを変えないため） |
| `wlanap_channel` | 1〜13（既定6） |
| `wlanap_idle` | 通信がないときに子機に戻るまでの秒数（既定600。0で戻らない） |
| `wlanap_gpio` | ボタンのGPIO（既定6）。`off` でボタンを見張らない |

`wlanpass` が8〜63文字でないとき（暗号なしのネットワークにつないでいたときなど）は、`wlanap_pass` がなければ暗号なしのAPになる。気になるなら `wlanap_pass` を決めておく。

## ボタンのGPIO（純正のファームウェアから）

- 純正のアプリ `cmsMain.out`（appfs）に、基板ごとの設定表が2つある。赤外LEDのGPIOがこの基板（GPIO6_4、[../../night/README.md](../../night/README.md)）と合うほうでは、ボタン（keystruct）は `0x80000006`、つまり **GPIO0_6（gpio6）**。もう一方の表ではGPIO9_2（gpio74）。
- 純正のアプリは50 msごとにボタンを読み、3秒を超えて押されると、AP用のドライバ `mt7601Uap.ko` に切り替えていた（ログの文字列 `3518 keydown time(%d),do cmsChangeWifiDevice2AP`）。
- 純正のピンの表では、GPIO0_6のピン多重化（レジスター `0x200F0138`）は0（GPIO）。OpenIPCの起動直後は1なので、`wifi-ap button` が0にする（ログ: `pin mux 0x200F0138: 0x00000001 -> 0`）。`wlanap_gpio=74` のときは `0x200F00D0` を1にする。
- 押していないときのレベルは、見張りを始めたときに覚える（2台目では0）。どちらの極性でも動く。同じレベルが1分続いたら、それを押していないときのレベルとし直す（押したまま起動したときなど）。
- 見張りはシェルで0.2秒ごとにGPIOを読む。CPUは約1.6%（197秒で3.11秒）。

ボタンを実際に押しての確認はまだしていない（手元から押せないため。値を書き換えたGPIOで判定を確かめた。下記）。押しても何も起きないときは、`wifi-ap probe 20` を動かしてボタンを3秒押しては離し、変わったGPIOを `fw_setenv wlanap_gpio 番号` で入れて再起動する。

## 仕組み

| ファイル | 役割 |
| --- | --- |
| `/etc/init.d/S97wifiap`（[S97wifiap](S97wifiap)） | 起動時に `wifi-ap button`（ボタンの見張り）を裏で動かす |
| `/usr/sbin/wifi-ap`（[wifi-ap](wifi-ap)） | APへの切り替えと戻し、`join`、通信の見張り。BusyBoxのシェルスクリプト |
| `/usr/lib/wifi-ap/mt7601Uap.ko.xz`（[mt7601Uap.ko.xz](mt7601Uap.ko.xz)） | AP用のドライバ（xz圧縮、214,352バイト） |

APにするとき: wpa_supplicantとwlan0のudhcpcを止め、子機用のドライバ `mt7601sta` を外し、AP用のドライバを `/tmp` に展開して読み込む（読み込んだら消す）。ドライバの設定（パスフレーズ入り）は `/tmp/mt7601ap/RT2870AP.dat` に書き、`ra0` を上げたらすぐ消す。`ra0` に `192.168.4.1/24` を付け、BusyBoxのudhcpdで `192.168.4.100`〜`150`（8台まで）を配る。戻すときはその逆で、最後に `ifup wlan0`（envの `wlanssid` と `wlanpass`）。途中で失敗したら子機に戻す。wlan0が戻らなければ再起動する。パスフレーズはログにも `status` にも出さない。2つの操作が重ならないよう、`/tmp/wifi-ap.lock` で排他する（ボタンは、ほかの操作の最中なら無視する）。

AP用のドライバ: 純正の `mt7601Uap.ko` はkernelが違うのでOpenIPCでは読み込めない。OpenIPCが子機に使っているMT7601Uのドライバのソース（[github.com/openipc/mt7601u](https://github.com/openipc/mt7601u)、commit `0ac46553`）にはAPの部分も入っているので、それをAPだけのモジュールとして、OpenIPCのkernel（hi3518ev100）とツールチェーンでビルドした。変更は [mt7601Uap.patch](mt7601Uap.patch)。[build-mt7601Uap.sh](build-mt7601Uap.sh) で同じものを作り直せる（ダウンロードはcommitとsha256で固定。別々の場所で2回作り、バイト単位で一致した）。GPL-2.0。

```
sh build-mt7601Uap.sh /tmp/mt7601Uap-build    # x86_64のLinux、約1 GB
```

## 入れ方

[../../easy/post_setup.py](../../easy/post_setup.py)（版4）が、ほかの設定と一緒にこの3つを入れる（[../../easy/README.md](../../easy/README.md)）。オーバーレイに約280 KiBの空きが要り、なければ報告して何も書かない（2台目では608 KiB → 416 KiB）。入れた後の再起動から動く。工場出荷リセットはオーバーレイごと消すので、その後は `post_setup.py` をもう一度実行する。

## 実機での確認（2台目、2026-11-17）

`post_setup.py`（版4）で入れ、`--reboot` で再起動した。再起動の後、見張りが動き、`pin mux 0x200F0138: 0x00000001 -> 0`、`gpio6 idle level: 0` と記録した。再起動後の `--check` は「変更なし」。

試験では、ホストでルーターのパスフレーズを扱わないよう、APのSSIDとパスフレーズを試験用の値にした（`WIFIAP_SSID`、`WIFIAP_PASS`）。既定の値（`OpenIPC-XXXX`、`wlanpass`）は `wifi-ap status` で確かめた。

| 項目 | 結果 |
| --- | --- |
| ボタンを押したときと同じ処理（`wifi-ap do-press`） | 7秒でAP（チャネル6、WPA2） |
| ホストからAPへ | 13秒でつながり、`192.168.4.100` を受けた。ping 3/3（平均3.9 ms）、SSH、Web画面（HTTP 200） |
| APから `wifi-ap join`（今のルーターのSSIDとパスフレーズ） | 14秒で子機に戻り、ルーターからアドレスを受けた（ログ。ホストからは9秒でAPが消えた）。ルーター経由でSSHがつながった |
| もう1回（だれもつながない） | `start` から8秒でAP。`stop` から10秒で子機に戻り、アドレスを受けた |
| 残ったプロセス | なし（udhcpd、通信の見張りとも） |
| env | 変わっていない（`join` は同じ値を書き直した） |
| kernelのoops、再起動 | なし |

ボタンの判定は、GPIOの値をファイルで置き換えて確かめた（同じ日、udhcpdを直す前の版の `wifi-ap` で。ドライバは今と同じソースからビルドしたもの。`WIFIAP_IDLE=40`）:

| 押し方 | 結果 |
| --- | --- |
| 1.0秒 | 無視（`hold it for 3-30 s`） |
| 3.5秒 | 切り替え。7秒でAP |
| 40秒間だれも通信しない | 子機に戻り、14秒でアドレスを受けた |
| 34.8秒 | 無視 |
| レベル1のまま1分 | これを押していないときのレベルとした |
| 逆の極性で3.5秒、もう一度3.3秒 | 6秒でAP、10秒で子機に戻った |

試験の途中で見つけて直したこと: カメラのBusyBox（1.36.1）のudhcpdは、設定の `pidfile` にpidを書かなかった。最初の版はそのpidでudhcpdを止めていたので、APをやめるたびにudhcpdが1つ残った（2台目で3つ。止めた）。今は `-f`（前面で動く）で裏に起動して、pidを自分で控える。

## 注意

- `iwconfig ra0` は、AP用のドライバでは `Segmentation fault` で終わる（kernelのoopsは出ず、APは動いたまま）。APの様子は `wifi-ap status` で見る。
- APの間は、ルーター（wlan0）からはカメラに届かない。
- APにつないだ人は、SSHとWeb画面のログイン画面まで届く（rootのパスワードは要る）。APのパスフレーズは、ルーターのパスフレーズを知らない人に教えない。
- MT7601は2.4 GHzだけ。

## 試験（オフライン）

`tests/test_wifi_ap.py`（48件）は、`wifi-ap` と `S97wifiap` をホストのBusyBoxで動かす。`fw_printenv`、`insmod`、`ip`、`devmem` などは模擬で、止める相手のプロセス（udhcpc、udhcpd、通信の見張り）は本物のプロセス。APの開始と停止、失敗したときに子機に戻すこと、パスフレーズがログに出ないこと、`join`（入力の検査、`wlanap_pass` の保存、アドレスが取れないときにAPに戻ること）、排他、通信の見張り、`status`、`probe`、ボタンの判定（押した時間、チャタリング、極性、覚え直し、gpio74）を確かめる。

```
cd research/openipc-migrate && python3 -m unittest tests.test_wifi_ap -v
```
