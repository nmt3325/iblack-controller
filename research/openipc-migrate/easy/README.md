# ネットワーク経由のOpenIPC導入をまとめて行う（easy/）

2台目の実機移行（[../README.md](../README.md) の「2台目の実機移行」）で順に手で行った作業を、2つのコマンドにまとめた。

| ファイル | 役割 | 確認状況 |
| --- | --- | --- |
| `easy_migrate.py` | 純正ファームのカメラを、SPIの控え → 配信 → diagnose → wdtgate → probe → 試験起動 → final → 起動確認 → 初回ログイン待ち → `post_setup.py` の順にOpenIPCへ移す。止まっても同じコマンドで続きから再開する | **オフライン試験だけ**（模擬カメラ相手の15件）。純正ファームのままの機体が手元にないので、実機では通していない |
| `post_setup.py` | 移行後の基板設定と更新ガードをSSHで入れる。何度実行してもよい | 2台目で実機確認（下記） |
| `config.example.json` | `easy_migrate.py` の設定例 | 読み込めることをテストで確認 |

カメラとのやり取りとフラッシュの規則は `serve_migration.py`・`build_stockboot.py`・カメラ側スクリプトのままで、`easy_migrate.py` はそれを順に呼ぶだけ。どの段階でもU-Bootは書かない。

## 使い方

```sh
cd research/openipc-migrate
C=~/iblack-private/easy.json      # 設定（リポジトリの外、0600）
python3 easy/easy_migrate.py --config "$C" check              # 事前確認だけ
python3 easy/easy_migrate.py --config "$C" run                # 試験起動まで。finalの前で止まる（終了コード4）
python3 easy/easy_migrate.py --config "$C" run --yes-final    # finalと起動確認。初回ログイン待ちで止まる（4）
#   表示に従い、所有者が ssh root@カメラ でEULAに答え、rootパスワードを決める
python3 easy/easy_migrate.py --config "$C" owner-done         # post_setup.pyを流して完了（0）
python3 easy/easy_migrate.py --config "$C" status             # どこまで済んだか
```

移行済みのカメラ（工場出荷リセットの後を含む）には `post_setup.py` だけを使う。

```sh
python3 easy/post_setup.py --ssh 'ssh root@192.0.2.20' --check    # 変える所を表示するだけ（あれば終了コード1）
python3 easy/post_setup.py --ssh 'ssh root@192.0.2.20' --reboot   # 入れて、必要なら再起動
python3 easy/post_setup.py --print > post_setup.sh                # カメラで流すスクリプトを書き出すだけ
```

## post_setup.py が入れるもの

| 対象 | 理由 |
| --- | --- |
| `/usr/sbin/sysupgrade`（更新ガード） | 標準のオンライン更新はこの配置を壊す（[../update/README.md](../update/README.md)）。元の更新処理は `/rom` に残る |
| envの `upgrade`（届かないURL） | ガードが消えた状態でも、オンライン更新が何も取得できないようにする |
| `/etc/modules` の `wdt` と読込み | 純正U-Bootが動かしたままのwatchdogに給餌する（ないと約40秒ごとに再起動） |
| envの `sensor=ar0141` | `load_hisilicon` が最後まで進む（`wdt.ko` もここで読まれる） |
| `/etc/sensors/ar0141_i2c_720p.ini`、`/etc/majestic.yaml` の `isp.blkCnt` | 720pの映像（[../video/README.md](../video/README.md)） |
| `majestic.yaml` の暗視設定、`S94lightsensor` | 昼夜の自動切替（[../night/README.md](../night/README.md)）。閾値は未設定のときだけ入れる |
| `S96vicheck` | 冷えた状態からの起動で映像が出ないときにmajesticを再起動する |
| `/usr/lib/libmajaudiofix.so`、`/etc/init.d/S95majestic` | マイクの音をOpusとAACで正しくする（[../audio/README.md](../audio/README.md)）。`S95majestic` がfirmwareのもの（`/rom` と同じ）か以前の版のときだけ入れ、違うものなら報告して残す |

すでに正しい項目は飛ばす。envは未設定の変数だけを埋め、違う値は報告して残す。`/proc/mtd` が移行後の固定配置でない、`/rom/usr/sbin/sysupgrade` がない、`fw_printenv` が読めない、のどれかなら何も変えない。U-Boot・kernel・rootfs・Wi-Fi設定は書かず、更新処理も動かさない。終了コードは0（完了・変更なし）、1（`--check` で変える所あり）、2（エラー）、3（`--ssh` のコマンド自体の失敗）。

2台目での確認：カメラの `/tmp` に作った仮のルートで、未設定の状態から7項目を入れ、2回目はファイルもenvも変えないことを確かめた（`wdt` の読込みだけは、仮のルートに `/proc/modules` がないので毎回行う）。実際のルートでは、手で入れてあった設定と一致し「変更なし」だった。マイクの修正（2ファイル）を足した版は、`--check` が変える所としてこの2つだけを挙げるのを見てから `--reboot` で入れ、再起動後の `--check` は「変更なし」だった（2026-11-17）。

## easy_migrate.py に必要なもの

すべてリポジトリの外に置く（`check` が確かめる）。

- **bundle**：`build_migration_appfs.py` で作る移行bundle（コマンドは [../README.md](../README.md) の「再実行」）。`--host` は、このホストがカメラに見せるIPv4アドレス。env画像は今の `make_env.py` で作る。既定で `sensor=ar0141` と届かない `upgrade` URLが入る。どちらかがないenvは `check` が止める（`sensor` がないと、2台目のように移行直後から約40秒ごとに再起動する）。
- **firmware**：固定したOpenIPCの `firmware.bin.hi3518ev100`（`build_stockboot.FIRMWARE_SHA256` と一致するもの）。**oipc_rootfs** はそのrootfsを展開した木。
- **auth_runtime**：試験起動用のWPA2ランタイム（[../auth/README.md](../auth/README.md)）。
- **wifi_vars**：0600の `key=value` ファイル（`wlanssid`、`wlanpass`）。試験起動と移行後のOpenIPCがつなぐ2.4 GHzのWi-Fiで、bundleのenvを作ったときと同じもの。
- **spi_dump**：そのカメラの8 MiBのSPIイメージ。なければ `backup` フックで作る。
- `mksquashfs`（squashfs-tools）と、フックが使うコマンド（例では `nmcli`）。
- **純正ファームへの配信手段**（`deliver` フック）。純正TCP3508の更新（`0x96`）で移行appfsを届ける送信ツールは、リポジトリに入れていない（[../README.md](../README.md)）。2台目では、カメラのAPモード経由でリポジトリ外の私的なツールを使った。SPIの読取り専用の控え（`backup` フック）も同じ。

## 設定ファイル

JSON。相対パスは設定ファイルからの相対。フックは実行したユーザーの権限で動くので、設定ファイルは自分の所有で、他人が書けないこと。未知のキーは拒否する（説明用の `comment` は可）。

| キー | 内容 |
| --- | --- |
| `bundle` `firmware` `oipc_rootfs` `auth_runtime` `wifi_vars` `spi_dump` | 上のもの |
| `work` | 状態・受信物・ログを置く私的なディレクトリ（0700で作る） |
| `mksquashfs` | 既定は `mksquashfs` |
| `camera_ssh` | `post_setup.py` に渡すSSHコマンド。`{ip}` がカメラのアドレスになる。既定は `ssh root@{ip}` |
| `hooks` | 下のフック |

## フック

環境ごとに違うネットワークの操作はフックとして設定に書く。`/bin/sh -c` で `work` をカレントにして動き、標準入力はない。出力は `work/logs/hook-名前-日時.log` に残る。環境変数 `EASY_STEP`（段階名）、`EASY_WORK`、`EASY_BUNDLE`、`EASY_SPI_DUMP`、`EASY_WIFI_VARS`、`EASY_HOST` と `EASY_PORT`（bundleのホストとポート）が渡る。1回30分で打ち切る。

| フック | 必須 | いつ・何をするか |
| --- | --- | --- |
| `camera_ap_up` | ○ | カメラとつながっていないとき（このホストに `EASY_HOST` がない、または `camera_ap_check` が失敗）。カメラのAP（純正ファームのAPモード）につなぎ、`EASY_HOST` を付ける |
| `camera_ap_check` | | 終了コード0ならつながっている。なければアドレスの有無だけで判断する |
| `backup` | | `spi_dump` がないときだけ。読取りだけで `$EASY_SPI_DUMP` に8 MiBのイメージを書く |
| `deliver` | ○ | 移行appfs（`$EASY_BUNDLE`）を純正ファームに1回だけ届ける。成功したら再実行でも送り直さない（`run --again deliver` で送り直す） |
| `trial_ap_up` | ○ | 試験起動の直後。このホストを `wifi_vars` のWi-Fi（試験のOpenIPCがつなぐ先）に `EASY_HOST` で参加させる。試験の報告はそこに届く |
| `trial_ap_down` | ○ | 試験の報告の後（失敗時も）。元に戻す。カメラのAPには `camera_ap_up` でもつなぎ直す |
| `final_ap_up` | ○ | finalの後。移行後のカメラがつなぐネットワーク（envの `wlanssid`）にこのホストを参加させる |
| `camera_address` | ○ | 移行後のカメラのIPv4アドレスを1行目に出す（DHCPのリースなど）。見つかるまで10秒ごとに最大15分呼ぶ |

`config.example.json` は2台目と同じ形：ホストはNetworkManagerで、カメラのAPに固定アドレスでつなぐ。試験起動と移行後は、ホスト自身が `wifi_vars` のSSIDでAPを立て、DHCPも配る（共有モード）。移行後に自宅のWi-Fiへ移すのは [../wifi/README.md](../wifi/README.md) の手順で行う。プロファイルの例：

```sh
# カメラのAP（純正ファームのAPモード）。BSSIDを固定し、bundleの --host を付ける
nmcli connection add type wifi ifname wlan0 con-name iblack-camera-ap autoconnect no \
  ssid 'CAMERA_AP_SSID' 802-11-wireless.bssid 'CAMERA_AP_BSSID' \
  ipv4.method manual ipv4.addresses 192.0.2.10/24 ipv4.never-default yes ipv6.method disabled
# ホストのAP（wifi.varsのSSIDとパスフレーズ、2.4 GHz、WPA2-PSK/CCMP）
nmcli connection add type wifi ifname wlan0 con-name iblack-host-ap autoconnect no \
  ssid 'TRIAL_SSID' 802-11-wireless.mode ap 802-11-wireless.band bg \
  wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp wifi-sec.psk 'TRIAL_PSK' \
  ipv4.method shared ipv4.addresses 192.0.2.10/24 ipv6.method disabled
```

`192.0.2.10` は説明用のアドレス（実際はbundleの `--host`）、`wlan0` は仮のインターフェース名。カメラのAPにパスフレーズがあれば `wifi-sec.*` を足す。

## 段階

| 段階 | 内容 | カメラに書くもの |
| --- | --- | --- |
| backup | SPIイメージを確かめて0600にする（なければ `backup` フック） | なし |
| deliver | `deliver` フックで届け、カメラからの呼出しを待つ | 純正の更新処理がappfsとbakfsを書く |
| diagnose | 読取り専用の診断。SPIイメージと照合する | なし |
| wdtgate | 全プロセスとwatchdogを5分止め、再開する | なし |
| probe | 純正U-Bootにenvを1回保存させ、純正で起動し直して読み戻す | 純正env |
| trial | 予備領域からOpenIPCを1回だけ起動し、Wi-Fi・DHCP・ホスト到達を確かめて純正に戻る | bakfs（試験イメージ）、純正env |
| final | `--yes-final` のときだけ。OpenIPCをkernel・rootfs領域へ書き、最後に純正env | kernel・rootfs・env（U-Bootは書かない） |
| boot | `final_ap_up`、`camera_address` の後、SSH(22)・HTTP(80)・RTSP(554)を確かめる | なし |
| owner | 初回ログインを所有者に頼んで止まる | なし |
| post_setup | `post_setup.py --reboot` | 上の表のもの |

- 済んだ段階は `work/easy-migrate.json` に記録し、`run` は最初の未完了の段階から続ける。同じ `work` で2つ同時には動かない。
- probeと試験起動は1回限りの仕掛け。仕掛けた後に止まっても、再実行は読み戻しから続け、仕掛け直さない。新しい試験起動は `run --again trial`。
- `run --until 段階` はその段階の後で止まる。`run --again 段階` はやり直す：deliver・diagnose・wdtgate・probe・trialは、finalが後戻りできない点（PONR）を越える前だけ（その後の段階もやり直しになる）。bootとpost_setupはいつでも。
- `restore-env` は、final前に途中でやめるとき、diagnose時の純正envをカメラに書き戻す。その後の `run` はdiagnoseからになる。
- finalが失敗しても、PONRの前ならカメラは純正のまま起動する（終了コード1）。PONRを越えて照合済みにならなければ終了コード3：カメラの電源を切らずに `run --yes-final` をもう一度実行する（カメラはfinalを待っている。1回の実行の中でも、finalを最大3回出し直す）。それでも終わらなければSPIで書き戻す。
- diagnoseでカメラのU-Bootが、SPIイメージと区画の最後の32バイトだけ違う（イメージ側が0xFF）場合は、カメラ自身のU-Bootとイメージの残りを合わせた `work/spi-reference-8m.bin` を作る（2台目で実際にあった）。SPIで書き戻すときはこちらを使う。

## 終了コード

0は完了（または `--until` に到達）、1は段階の失敗（表示に断りがなければ純正のまま起動する）、2は設定や事前確認の問題、3はfinalがPONRを越えて照合済みでない（電源を保って `run --yes-final`）、4は所有者の操作待ち（`--yes-final` か初回ログイン）。

## 作業ディレクトリ（work）

`easy-migrate.json`（進み具合）、`state/`（`build_stockboot.py` の状態）、`uploads/`（カメラからの受信物）、`actions/`（action一式）、`logs/`（段階とフックのログ）、`tmp/`、`camera-address.txt`、`spi-reference-8m.bin`。どれも私的なデータで、Gitに入れない。

## 守ること

- 初回ログイン（Majestic EULAへの同意とrootパスワード）は所有者が自分で入力する。カメラ自身が所有者本人の入力を求めており、このツールは代行しない。質問は約6秒で打ち切られるので、間に合わなければログインし直す。
- Web画面のファームウェア更新と自動更新は使わない。安全な更新の方法は [../update/README.md](../update/README.md)。
- 工場出荷リセット（Web画面のリセット、`sysupgrade -n`、`firstboot`）はオーバーレイごと設定と更新ガードを消す。リセットの後は `post_setup.py` をもう一度実行する（envは残る）。

## テスト

カメラにもネットワークにも触れない。

```sh
cd research/openipc-migrate
python3 -m unittest tests.test_easy_migrate tests.test_post_setup -v
```

`test_easy_migrate`（15件）は、`serve_migration.py` のプロトコルを話す模擬カメラと、`build_stockboot.py` の模擬を相手に、全段階の通し・再開・1回限りの仕掛け・finalのPONR越えと再実行・`restore-env`・事前確認・古いenvの拒否・設定の検査・SPI参照イメージ・設定例の読込みを確かめる。`test_post_setup`（16件）は、生成したスクリプトをホストのBusyBoxで、工場出荷状態をまねた仮のルートと模擬の `fw_printenv`・`fw_setenv` を相手に動かす（マイクの修正を、firmwareの `S95majestic` か以前の版の上にだけ入れることも確かめる）。
