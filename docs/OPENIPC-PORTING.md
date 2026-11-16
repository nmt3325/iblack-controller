# iBlack → OpenIPC：ネットワーク移行の調査

## 判断

> 2026-11-15の状態：試験起動の後にネットワーク経由のfinalを実行したが、書込みの途中でハードウェアwatchdogによりリセットされ、カメラは起動できない（SPIで復旧する）。この節の記述はそれ以前の判断である。経緯と復旧方法は [`research/openipc-migrate/README.md`](../research/openipc-migrate/README.md) にある。

**Telnetログインなしでも、純正TCP3508更新サービスの `0x96` → 移行用appfs → RAM上の移行処理、という導入経路は成立する見込みがある。そのうちLAN上のホストから進めるオンライン移行処理を、純正U-Bootを残す方式で実装し、x86とqemu-armでのオフライン検証まで終えた。ただし、実機移行を許可できる状態ではない。**

この個体はWi-Fiしか使えない（UARTもEthernetもない）。SPIでの読み書きはでき、8 MiB全体のダンプがある。そのため、この基板で起動したことのないOpenIPC U-Bootは書かない。純正U-Bootと純正envを残し、OpenIPCはその後ろへずらして置く。本番の前に、読取り専用のdiagnose、純正U-Bootがこちらのenvを実行できるかを見るprobe、自動で純正へ戻る1回だけの試験起動を挟む。

この段階で確認したのは、純正ARMコードを使った実サイズのオフライン更新試験、取得済みOpenIPCイメージの構造・配置・実装、そして移行処理一式（[`research/openipc-migrate`](../research/openipc-migrate/README.md)）の動作である。カメラへ新しいイメージを送っていない。フラッシュ書込み・再起動も行っていない。移行appfsを `0x96` で届ける送信部は、意図して実装していない。

中止条件は現在も **`flash_write_allowed: false`**。主な理由は、既存bakfsの読出し不一致、純正U-Bootがこちらのenvを実行できるかとOpenIPC kernelがこの基板で起動するかの未確認（それぞれprobeと試験起動で確かめる）、RAM-root／watchdog引継ぎの未検証である。

調査資料・再現コード・機密除去済みの結果はこのGitリポジトリを正本とする。Notionへのファイル添付は行わない。元ダンプ・個体設定・秘密値・PCAP・送信用更新データはGitに入れない。

## オンライン移行の実装

実装と再実行の手順は [`research/openipc-migrate`](../research/openipc-migrate/README.md) にある。確認はすべてオフライン（x86ホストと、qemu-armのユーザーモードでの7検査）で、実機では動かしていない。以前の、OpenIPC U-Bootを含めて8 MiB全体を書き換える実装（コミット `b66cf56`）は、UARTのないこの個体には向かないので削除した。

| 項目 | 結果 | 限界 |
| --- | --- | --- |
| 配置 | 純正U-Boot（`0x000000`）と純正env（`0x040000`、128 KiB）は残す。OpenIPCのenvは `0x060000`、公開firmwareは加工せずに `0x070000`、overlayは896 KiB。kernelの余裕は35,812 B、rootfsの余裕は11,462 B | 標準と違う配置なので、移行後も `sysupgrade -u` は使えない |
| 移行appfs | 2,613,248 B。`0x96` 経路の上限2,621,432 Bに対して余裕は8,184 B。純正の既存58項目のうち変更は `runapp.sh` だけ（起動行と試験起動の分岐） | 送信部は未実装。`0x96` はbakfsも書き換える |
| 段階 | diagnose（読取りのみ）→ probe（純正U-Bootがenvを実行して `saveenv` できるか）→ 試験起動（bakfs領域からOpenIPC kernelを1回だけ起動し、次は純正）→ final。前の段階が通っていなければ、ホストは次の段階を作らない | 各段階に個別の承認が要る |
| 純正U-Bootのモデル | 純正U-Boot 2010.06のコマンド解釈（`CONFIG_SYS_CBSIZE` 1024、引数16個まで）と `_do_setenv`、Linux 3.0.8の `cmdlinepart` を、バイナリとソースから読み取ってモデル化した。試験用 `bootcmd` は417文字（上限1,023文字） | モデルであり、実機では動かしていない |
| 試験起動 | 試験用SquashFS（Wi-Fiモジュール、設定、tlの雛形、スクリプト）は520,192 B（枠704 KiB）。runapp.shの分岐、900秒タイマー、ペイロードがなければすぐ再起動することを、qemu上の純正shでも確かめた | 実kernel、実Wi-Fi、実フラッシュは未検証 |
| phase1・phase2・installer（qemu） | 純正BusyBox 1.18.4とOpenIPC BusyBox 1.36.1の実物で、ポーリング、取得と照合、各段階の書込みと照合を通した。書込み先は模擬フラッシュ | 実MTDドライバ、電源断は未検証 |
| x86のユニット・E2E | 135件が成功 | BusyBoxはDebian版で代用 |

検証で見つけて直した不具合：純正U-Bootの `_do_setenv` は、envの終端より後ろを消さない。試験起動の後のenvに残る古い `bootcmd` を読んで「試験用のまま」と誤判定していたので、終端で読むのをやめた。純正BusyBox 1.18.4の `dd` は `conv=` などを、`grep` は `-x` を受け付けず、`wget --post-data` は本文を%XX形式にして送る。`trial.sh` のログ書込みと `saveenv` 失敗時の純正envの書き戻しが失敗し、ホストへの到達も必ず失敗と報告し、届いた試験ログもホストで読めないところだった（qemu上の実物で発見）。3件とも直し、ビルド時の検査とE2Eの `dd`・`grep`・`wget` も純正と同じ動作に揃えた。以前の方式で直した2件（OpenIPCの `wget --post-file` が最初のNULで止まる、純正shが `$((...))` なしでビルドされている）も引き継いだ。ほかに、検証側の誤りと、検証を止めたときの後始末を直した。

## 配置・純正更新経路の検証（前段）

| 調査 | 結果 | 限界 |
| --- | --- | --- |
| 2,609,152バイトの診断用appfsを純正受信・復号・更新経路へ入力 | 仮想appfsと仮想bakfsの内容が期待値と一致。再起動要求直前まで到達 | 実機受信・起動・物理書込みの証明ではない |
| ヘッダCRC・暗号文CRC・平文CRCの異常 | 3ケースとも仮想消去・書込みなし | CRCは実機のフラッシュ信頼性を保証しない |
| bakfs収容上限ちょうど／上限+1バイト | 上限ちょうどは成功。+1では事前拒否されず書込みに進み、仮想MTD境界検査で停止 | 実機ドライバの短い書込み／エラー処理は未検証 |
| splitイメージのCRC・SquashFS配置・旧MTD跨ぎ | 構造検査と19ユニットテスト成功 | 実カーネルの起動やMTD登録はエミュレーションしていない |
| 対象カーネルの内部gzip展開 | split NOR登録関数と自動root選択の文字列を確認 | 文字列の存在は実機での実行成功ではない |
| MT7601接続方式 | `mt7601u-hi3518ev100-camhi` プロファイルが既に `mt7601sta` をロード | この基板でのUSB認識・接続・再接続は未試験 |

結果：[`fullsize-appfs-proof.json`](../research/openipc-port/results/fullsize-appfs-proof.json)、[`flash-layout.json`](../research/openipc-port/results/flash-layout.json)、[`runtime-inventory.json`](../research/openipc-port/results/runtime-inventory.json)。

## 1. ハードウェアの見立て

- **SoC：Hi3518EV100が最有力**。純正U-BootのDDR／PLL表はEV100 universal U-Bootと125/125項目一致したという前段調査に基づく。今回は再測定していない。チップ刻印は未確認。
- RAM：64 MiB、NOR：8 MiBという前段調査を前提とする。Linuxが使えるRAMと空き容量は別であり、移行時の実測が必要。
- センサー：AR0141。前段調査では純正初期化表とOpenIPC `libsns_ar0140.so` の表が一致。2台目のOpenIPCで映像を確認した（720pのH.264、約27 fps。Bayer配列はGRBG）。iniと設定は [`research/openipc-migrate/video`](../research/openipc-migrate/video/README.md)。
- Wi-Fi：純正はMT7601系列。対象OpenIPC rootfsには `lib/modules/3.0.8/extra/mt7601sta.ko` がある。
- 外部接続：Wi-Fiだけ（UARTもEthernetもない）。SPIでの読み書きはでき、8 MiB全体のダンプがある（所有者が保管し、Gitには入れない）。

## 2. 純正アップデートを入口にする場合

純正起動経路は `inittab → rcS → S10mpp → cmssysrun.sh → /app/runapp.sh`。`/app` は純正appfsである。ここへ所有者の診断コードを配置することが、Telnetに依存しない実行経路の候補になる。

`0x96` はappfsだけの一時更新ではない。仮想試験では `/dev/mtd2` と `/dev/mtd5` を消去・更新し、最後に再起動要求へ到達した。**元のアプリとバックアップを同時に変更するため、bakfsを無傷の復旧先として残せる方法ではない。**

### 実サイズ試験

- 診断用appfs：**2,609,152 B**。
- bakfsには8バイトの長さ／CRCヘッダが付く。
- appfs/bakfsモデル：各 `0x280000` = 2,621,440 B。
- bakfsへの最大収容画像：**2,621,432 B**。
- 今回画像の余裕：**12,280 B**。
- モデルの消去単位は64 KiB。これは仮定であり、実機の `MEMGETINFO` で測った値ではない。

上限+1 Bの入力ではappfsの更新後、bakfsも変更され、最終的にbakfs末尾 `0x280000` から1 Bを書こうとした。厳格な仮想MTD境界検査がそこで止めた。実機で隣接領域が上書きされると断定するものではないが、**容量超過が変更前に安全に拒否されない**ことは移行設計上の問題である。

既存 `pack_update.py` の `0x94`／小容量ガードは変更していない。今回の大容量envelopeはエミュレータ内のメモリだけに生成し、保存・送信する機能を追加していない。

## 3. 対象OpenIPCとフラッシュ配置

推奨候補：**`hi3518ev100-nor-lite-split`**。取得物のSHA-256・サイズは [`source-manifest.json`](../research/openipc-port/source-manifest.json) に記録する。`latest` は可変なので、名前だけを信用して再取得しない。

- combined firmware：7,012,352 B = `0x6b0000`。
- kernel uImage：1,864,732 B。ヘッダCRC・データCRCとも正常。
- uImageの圧縮IDは0だが、中身は自己展開zImageであり、その中にgzip圧縮カーネルがある。「Linux本体が無圧縮」という意味ではない。
- SquashFSはfirmware内 `0x1d0000` に始まる。公開単体rootfsと同じ内容。

### OpenIPCの標準配置（この方式では使わない）

| 領域 | 開始 | 終了（含まない） | サイズ |
| --- | --- | --- | --- |
| boot | `0x000000` | `0x040000` | 256 KiB |
| env | `0x040000` | `0x050000` | 64 KiB |
| kernel | `0x050000` | `0x220000` | 1,856 KiB枠 |
| rootfs | `0x220000` | `0x700000` | 4,992 KiB枠 |
| rootfs_data / overlay | `0x700000` | `0x800000` | **1 MiB** |

`firmware` 仮想パーティションは `0x50000` からフラッシュ末尾までを覆い、overlayも含む。7,012,352 Bは今回の配布画像の長さであり、仮想パーティション全体の長さではない。

### この方式の配置

公開firmwareは加工せず、標準より `0x20000` 後ろの `0x070000` に書く。純正U-Bootと純正envの384 KiBは、読取り専用の `boot` 区画として残す。

| 領域 | 開始 | 終了（含まない） | サイズ |
| --- | --- | --- | --- |
| boot（純正U-Boot＋純正env、読取り専用） | `0x000000` | `0x060000` | 384 KiB |
| env（OpenIPCの `fw_printenv` 用） | `0x060000` | `0x070000` | 64 KiB |
| kernel | `0x070000` | `0x240000` | 1,856 KiB枠（使用1,864,732 B） |
| rootfs | `0x240000` | `0x720000` | 4,992 KiB枠（使用5,100,346 B） |
| rootfs_data / overlay | `0x720000` | `0x800000` | 896 KiB |

### 純正MTDから見た書込み範囲

| 純正MTD | そのMTD内のオフセット | 長さ | 内容 |
| --- | --- | --- | --- |
| mtd0 / boot | `0x040000` | `0x020000` | 純正env（finalの最後に書く） |
| mtd0 / boot | `0x060000` | `0x010000` | OpenIPC env |
| mtd0 / boot | `0x070000` | `0x190000` | firmware（kernelの前半） |
| mtd1 / rootfs | `0x000000` | `0x0c0000` | firmware |
| mtd2 / appfs | `0x000000` | `0x280000` | firmware |
| mtd3 / mdat1 | `0x000000` | `0x020000` | firmware |
| mtd4 / mdat2 | `0x000000` | `0x020000` | firmware |
| mtd5 / bakfs | `0x000000` | `0x1a0000` | firmware |
| mtd5 / bakfs | `0x1a0000` | `0x0e0000` | overlay（消去） |

純正U-Boot（mtd0の `0x000000`–`0x03FFFF`）は書かない。これは配置の説明であり、実行用の書込みコマンドではない。個体設定のmdat領域も上書き対象になる。移行前の私的な退避が必須で、内容はGitにもログにも出さない。

前段で読出し不一致があったフラッシュ位置 `0x6c4f28` は、試験起動ではOpenIPC kernel（tk+`0x144f28`）、移行後はrootfs+`0x484f28`（使用中のSquashFSデータ内）に当たる。**配置をずらしても当該位置を使わなくなるわけではない。** diagnoseはこのブロックを16回読み、1回でも違えば先へ進まない。試験起動は `verify=y` で起動するので、kernelをこの位置で読み違えればCRC不良で純正へ戻る。ただし部品レベルの原因は未確定なので、異常がその物理アドレスに追従すると証明したものではない。

### U-Boot／環境領域

純正envは `0x40000..0x60000` の128 KiBで、OpenIPCの標準配置（kernelが `0x50000`）とは64 KiB重なる。標準配置にするには互換ブートローダと64 KiB envへの移行が要るが、この個体では起動実績のないブートローダを書けない。そこで純正U-Bootと純正envを残し、OpenIPC用の64 KiB envを `0x60000`、firmwareを `0x70000` に置く。どちらも純正の配置では使われていない領域から始まる。純正U-Bootは自分のenvの `bootcmd` でkernelを `0x70000` から読み、`bootargs` で `mtdparts=` と `root=/dev/mtdblock3` を渡す。OpenIPC側の `fw_setenv` は `0x60000` のenvだけを書くので、純正U-Bootの起動設定は変わらない。

OpenIPCのkernelは、`0x50000` にuImageヘッダ（続いてSquashFS）があるとsplit配置とみなし、`mtdparts=` とMTDを指す `root=` を無視して自分の表を使う。kernel本体の文字列（`mtd_split_layout_register`、`split NOR layout: root on mtdblock%d`）と、対応するOpenIPCのkernelパッチで確認した。この方式では `0x50000` は純正envの中にあり、そこは印字可能な文字列とゼロ埋めだけなので、uImageヘッダは入り得ない（SPIダンプでもゼロ）。したがって、こちらが渡す `mtdparts=` と `root=` が使われる。書くenvごとにこの点も検査する。実機での起動試験ではない。[^https://github.com/OpenIPC/wiki/blob/master/en/nor-split-layout.md]

公開デフォルトは `osmem=32M` であり、物理64 MiB全部をLinuxに渡せばよいわけでもない。移行後の `bootargs` は `mem=32M` とした。MMZ等の予約も含めた確認が必要。

## 4. Wi-Fi・センサー・更新用ランタイム

### Wi-Fi

`etc/wireless/usb` の既存プロファイルは次の区別がある。

- `mt7601u-generic` → `modprobe mt7601u`。
- **`mt7601u-hi3518ev100-camhi` → `modprobe mt7601sta`**。

前段の「独自wlandevプロファイルが必要」という見立ては修正する。対象SoC用の既存候補はあるため、最初から独自プロファイルを作る必要があるとは限らない。ただし別メーカー基板向けという名称であり、この個体のUSB・GPIO・インターフェース名・認証方式での接続は未確認。`S40network` は環境変数 `wlandev` を読み、接続設定は `wlanssid`／`wlanpass` から生成する。supplicantは `nl80211,wext` の順で利用する。これらの名前は設定方式の説明であり、実際のSSID・パスフレーズ・個体MAC等はGit管理しない。

### センサー

`load_hisilicon` は `ar0140 | ar0141` に24 MHzクロック設定を持つ。ライブラリだけでなく、AR0141用ini、I2C、解像度・フレームレート・GPIOを合わせて検証する必要がある。2台目では、envの `sensor=ar0141`、AR0141用ini（`libsns_ar0140.so`、GRBG）、`isp.blkCnt: 9` で映像が出た。起動して最初のmajesticだけはVIがフレームを受けない（センサーがスタンバイのうちにVIを有効にするため）ので、`S96vicheck` が1回起動し直す。詳細は [`research/openipc-migrate/video`](../research/openipc-migrate/video/README.md)。暗視は、IRカットフィルターがGPIO0_2、赤外LEDがGPIO6_4、明るさセンサーがADCのチャネル1で、majesticの `nightMode` で昼と夜を自動で切り替える（[`research/openipc-migrate/night`](../research/openipc-migrate/night/README.md)）。

### RAM-rootとwatchdog

対象OpenIPCの `sysupgrade` は1.0.84で、RAM-rootの作成、BusyBoxと依存ライブラリのコピー、`chroot` 事前確認、`pivot_root`、旧rootからの解放、watchdogの引継ぎを実装している。watchdog保持処理は所有プロセスを調べ、必要なら約3秒おきにkeepaliveする。

これを純正上でそのまま起動してよいという意味ではない。純正BusyBoxのアプレット不足・uClibc ABI・PID1・マウント構成・watchdog設定に合わせた、検証済み移行ランタイムが別途必要。単に `/tmp` にスクリプトを置くだけでは、実行中のシェル／共有ライブラリが消去対象に残り得る。

実装した移行処理では、OpenIPCのBusyBoxとmuslローダーを `/dev/om`（RAM）に置き、BusyBoxのPT_INTERPを `/dev/om/ld-musl.so` に書き換えて、純正rootfsの隣で動かす。finalで後戻りできない点を越えた後は終了せず、finalの再試行とcollectだけを受け付ける。watchdogデバイスがある場合、今の実装は純正アプリの停止を拒否する。実機にwatchdogがあれば、引継ぎを別に設計する必要がある。実機にはwatchdogデバイスがなかったが、純正 `cmsMain.out` が `/dev/mem` 経由でハードウェアwatchdogへ給餌していた。finalはこれを止めたため、書込みの途中でリセットされた。

finalでは、firmwareと2つのenv画像（64 KiB＋128 KiB）の7,208,960 BをRAMに置く。これはバイナリの最低ステージ量であり、RAM-root、復号バッファ、プロセス、ページキャッシュ等を含むピーク必要RAMではない。「空き約22 MBなら十分」とはまだ結論しない。移行処理は、空きRAMの推定が12 MiB未満か、`/dev/om` の空きが10 MiB未満なら始めない。このしきい値は実測前の仮の値である。

## 5. 未解決事項と次の順序

1. **既存読出し不一致を優先**：独立した実機読出し経路でSPI/MTD、RAM/DMA、ドライバを切り分ける。7回の詳細は [bakfs調査](BAKFS-READBACK-INVESTIGATION.md)。フラッシュ素子の故障を確定したわけではない。
2. **`0x96` で移行appfsを届ける（要承認。送信部は未実装）**：appfsとbakfsを両方書き換えて再起動する。bakfsは無傷の復旧先として残らない。
3. **diagnose（読取りのみ）**：全128ブロックを3回ずつ読み、`0x6c0000` のブロックは16回読む。SPIダンプとの照合（純正U-Boot、純正env）、MTD表、MT7601（`148f:7601`）、watchdogデバイスの有無、空きRAMを確かめる。
4. **probe（要承認）**：純正envをprobe用のenvに替えて再起動し、純正U-Bootがそれを実行して `saveenv` し、純正で起動することを確かめる。途中で電源が切れてenvのCRCが壊れても、純正U-Bootの既定env（純正配置）で純正が起動する。
5. **試験起動（要承認）**：bakfs領域に試験イメージ（OpenIPC kernel＋試験用SquashFS）を書き、最後に試験用envを書いて再起動する。純正U-Bootは純正の `bootcmd` を書き戻して `saveenv` してから、OpenIPC kernelを1回だけ起動する。純正ユーザーランドのまま、OpenIPCのWi-Fiドライバで接続してホストへ報告し、再起動で純正へ戻る。kernelがユーザーランドに着く前に止まったときは900秒のタイマーが動かないので、手で電源を入れ直す（純正に戻る）。この後はbakfsに純正appfsの控えが残らない。
6. **final（要承認）**：OpenIPC env → firmware → overlay消去 → 最後に純正env。firmwareの `0x080000` 以降を書き始めてから純正envを書き終えるまで（試験起動の書込み速度から1〜2分の見込み）は後戻りできず、ここで電源が切れたらSPIで復旧する。2026-11-15のfinalは、この区間でwatchdogのリセットにより止まった。

final前なら、diagnose時に読んだ純正envへいつでも戻せる（`restore-env`）。どの段階でも、最後の復旧手段はSPIとダンプである。

移行後は、OpenIPCの `sysupgrade -u`（U-Boot更新）を使わない。`boot` 区画は読取り専用にしてあるが、ここには純正U-Bootと純正envがあり、書くと起動しなくなる。kernelとrootfsの更新は `sysupgrade -k -r`（区画を名前で探す）を候補とするが、この配置では未検証で、上の枠（kernel 1,856 KiB、rootfs 4,992 KiB）に収まるものに限る。設定はoverlayへ置き、意図しない `-n` を避ける。これは初回の純正→OpenIPC移行とは別工程である。

## 関連資料

- [再現手順と安全範囲](../research/openipc-port/README.md)
- [オンライン移行の実装と検証](../research/openipc-migrate/README.md)
- [既存の純正更新形式解析](FIRMWARE-SELF-UPDATE.md)
- [OpenIPC split NOR仕様](https://github.com/OpenIPC/wiki/blob/master/en/nor-split-layout.md)
- [EV100 U-Boot関連の既存課題](https://github.com/OpenIPC/firmware/issues/1542)
- [EV100基板依存の既存課題](https://github.com/OpenIPC/firmware/issues/2593)
