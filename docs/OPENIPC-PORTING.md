# iBlack → OpenIPC：ネットワーク移行の調査

## 判断

**Telnetログインなしでも、純正TCP3508更新サービスの `0x96` → 移行用appfs → RAM上の移行処理、という導入経路は成立する見込みがある。そのうちLAN上のホストから進めるオンライン移行処理は実装し、x86とqemu-armでのオフライン検証まで終えた。ただし、実機移行を許可できる状態ではない。**

この段階で確認したのは、純正ARMコードを使った実サイズのオフライン更新試験、取得済みOpenIPCイメージの構造・配置・実装、そして移行処理一式（[`research/openipc-migrate`](../research/openipc-migrate/README.md)）の動作である。カメラへ新しいイメージを送っていない。フラッシュ書込み・再起動も行っていない。移行appfsを `0x96` で届ける送信部は、意図して実装していない。

中止条件は現在も **`flash_write_allowed: false`**。主な理由は、既存bakfsの読出し不一致、新U-Bootの実基板未検証、RAM-root／watchdog引継ぎの未検証である。

調査資料・再現コード・機密除去済みの結果はこのGitリポジトリを正本とする。Notionへのファイル添付は行わない。元ダンプ・個体設定・秘密値・PCAP・送信用更新データはGitに入れない。

## オンライン移行の実装

実装と再実行の手順は [`research/openipc-migrate`](../research/openipc-migrate/README.md) にある。確認はすべてオフライン（x86ホストと、qemu-armのユーザーモードでの10検査）で、実機では動かしていない。

| 項目 | 結果 | 限界 |
| --- | --- | --- |
| 移行appfs（純正appfs＋`stage2.sh`＋`migrate.conf`＋起動行） | 実入力で2,613,248 B。`0x96` 経路の上限2,621,432 Bに対して余裕は8,184 B。純正の既存58項目のうち変更は `runapp.sh` だけ | 送信部は未実装。`0x96` はbakfsも書き換える |
| phase1（純正BusyBox 1.18.4のsh） | qemu上で実物の純正shとアプレットが、ポーリング、GO受信、runtimeの取得とSHA-1／MD5照合、phase2への受け渡しまで通った | x86ではARMバイナリをラッパー経由で起動した |
| phase2・installer・hook（OpenIPC BusyBox 1.36.1、musl） | qemu上で、配布物のSHA-256照合、純正アプリの停止、読出しの安定性確認、mdat退避のbase64送信と照合、4領域の書込みと最終照合を通した。書込み先はシミュレーション用のファイル | 実MTDドライバ、実フラッシュの消去・書込み、電源断は未検証 |
| x86のユニット・E2E | 103件と、インストーラーのシミュレーション26件が成功 | BusyBoxはDebian版で代用 |

検証で見つけて直した不具合が2つある。

- OpenIPCの `wget --post-file` は最初のNULで送信を止める。mdatの退避をそのまま送るとホストには先頭しか届かない。base64で送り、ホストが返す復号後のSHA-256を手元と照合してから書込みに進むようにした。
- 純正shは `$((...))` なしでビルドされており、最後の分岐に着く前にファイル内の関数をすべて解析する。phase2側の `$((...))` のせいで、phase1が始まる前に構文エラーで終わっていた。x86のE2Eでは見えず、qemuで実物の純正shを動かして見つかった。

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
- センサー：AR0141。前段調査では純正初期化表とOpenIPC `libsns_ar0140.so` の表が一致。実画像は未確認で、センサーiniは未作成。
- Wi-Fi：純正はMT7601系列。対象OpenIPC rootfsには `lib/modules/3.0.8/extra/mt7601sta.ko` がある。

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

### OpenIPC側の物理配置

| 領域 | 開始 | 終了（含まない） | サイズ |
| --- | --- | --- | --- |
| boot | `0x000000` | `0x040000` | 256 KiB |
| env | `0x040000` | `0x050000` | 64 KiB |
| kernel | `0x050000` | `0x220000` | 1,856 KiB枠 |
| rootfs | `0x220000` | `0x700000` | 4,992 KiB枠 |
| rootfs_data / overlay | `0x700000` | `0x800000` | **1 MiB** |

`firmware` 仮想パーティションは `0x50000` からフラッシュ末尾までを覆い、overlayも含む。7,012,352 Bは今回の配布画像の長さであり、仮想パーティション全体の長さではない。

### 純正MTDから見たfirmware配置

| 純正MTD | そのMTD内のオフセット | 書込み対象の長さ |
| --- | --- | --- |
| mtd0 / boot | `0x050000` | `0x1b0000` |
| mtd1 / rootfs | `0x000000` | `0x0c0000` |
| mtd2 / appfs | `0x000000` | `0x280000` |
| mtd3 / mdat1 | `0x000000` | `0x020000` |
| mtd4 / mdat2 | `0x000000` | `0x020000` |
| mtd5 / bakfs | `0x000000` | `0x180000` |

これは配置検証用の表であり、実行用書込みコマンドではない。個体設定のmdat領域も上書き対象になる。移行前の私的な退避が必須で、内容はGitにもログにも出さない。

前段で読出し不一致があったフラッシュ位置 `0x6c4f28` は、新配置ではrootfs領域内に入る。**split化しても当該位置を使わなくなるわけではない。** ただし部品レベルの原因は未確定なので、異常がその物理アドレスに追従すると証明したものではない。

### U-Boot／環境領域

純正envは `0x40000..0x60000` の128 KiBで、新firmwareと64 KiB重なる。したがって、純正envをそのまま使って将来も `saveenv` する設計は不可。互換ブートローダと64 KiB envへの移行が必要になる。

取得済みEV100 universal U-Bootは `0x50000` から3 MiBを読むため、今回のkernelは読出し範囲内に収まる。ただしDDR表の一致だけでこの基板で起動するとは保証できない。公開デフォルトは `osmem=32M` であり、物理64 MiB全部をLinuxに渡せばよいわけでもない。MMZ等の予約も含めた確認が必要。

**split対応kernelが検出できれば、固定配置用の `root=/dev/mtdblock3` や `mtdparts=` を見ただけで起動不可とは判断しない。** Hi3518EV100の対応kernelはsplit配置を検出し、自分でrootを選ぶ。公式仕様に加え、今回のkernel本体にも `mtd_split_layout_register`、`split NOR layout: root on mtdblock%d` 等を確認した。実機起動試験ではない。[^https://github.com/OpenIPC/wiki/blob/master/en/nor-split-layout.md]

## 4. Wi-Fi・センサー・更新用ランタイム

### Wi-Fi

`etc/wireless/usb` の既存プロファイルは次の区別がある。

- `mt7601u-generic` → `modprobe mt7601u`。
- **`mt7601u-hi3518ev100-camhi` → `modprobe mt7601sta`**。

前段の「独自wlandevプロファイルが必要」という見立ては修正する。対象SoC用の既存候補はあるため、最初から独自プロファイルを作る必要があるとは限らない。ただし別メーカー基板向けという名称であり、この個体のUSB・GPIO・インターフェース名・認証方式での接続は未確認。`S40network` は環境変数 `wlandev` を読み、接続設定は `wlanssid`／`wlanpass` から生成する。supplicantは `nl80211,wext` の順で利用する。これらの名前は設定方式の説明であり、実際のSSID・パスフレーズ・個体MAC等はGit管理しない。

### センサー

`load_hisilicon` は `ar0140 | ar0141` に24 MHzクロック設定を持つ。ライブラリだけでなく、AR0141用ini、I2C、解像度・フレームレート・GPIOを合わせて検証する必要がある。

### RAM-rootとwatchdog

対象OpenIPCの `sysupgrade` は1.0.84で、RAM-rootの作成、BusyBoxと依存ライブラリのコピー、`chroot` 事前確認、`pivot_root`、旧rootからの解放、watchdogの引継ぎを実装している。watchdog保持処理は所有プロセスを調べ、必要なら約3秒おきにkeepaliveする。

これを純正上でそのまま起動してよいという意味ではない。純正BusyBoxのアプレット不足・uClibc ABI・PID1・マウント構成・watchdog設定に合わせた、検証済み移行ランタイムが別途必要。単に `/tmp` にスクリプトを置くだけでは、実行中のシェル／共有ライブラリが消去対象に残り得る。

実装した移行処理では、OpenIPCのBusyBoxとmuslローダーを `/dev/om`（RAM）に置き、BusyBoxのPT_INTERPを `/dev/om/ld-musl.so` に書き換えて、純正rootfsの隣で動かす。書込み後は終了せず、純正initへ戻らない。watchdogデバイスがある場合、今の実装は純正アプリの停止を拒否する。実機にwatchdogがあれば、引継ぎを別に設計する必要がある。

今回のfirmware＋boot領域＋envだけで最低7,340,032 Bを保持する。これはバイナリの最低ステージ量であり、RAM-root、復号バッファ、プロセス、ページキャッシュ等を含むピーク必要RAMではない。「空き約22 MBなら十分」とはまだ結論しない。移行処理は、空きRAMの推定が12 MiB未満か、`/dev/om` の空きが10 MiB未満なら始めない。このしきい値は実測前の仮の値である。

## 5. 未解決事項と次の順序

1. **既存読出し不一致を優先**：独立した実機読出し経路でSPI/MTD、RAM/DMA、ドライバを切り分ける。7回の詳細は [bakfs調査](BAKFS-READBACK-INVESTIGATION.md)。フラッシュ素子の故障を確定したわけではない。
2. **所有者の確認を経た診断段階**：SoC刻印、実際のeraseサイズ・保護属性、利用可能RAM、watchdog所有者、USB認識を収集する。診断appfs自体も両アプリ領域の書換えと再起動を伴うため、無断で投入しない。
3. **RAM上だけのリハーサル**：全資材を先に取得・ハッシュ確認し、旧rootに依存しない実行環境、watchdog引継ぎ、設定の私的退避を確かめる。
4. **電源・復旧手段と新U-Bootの確認**：初号機はSPI等の独立復旧手段を用意するのが安全。TFTP/U-Boot復旧も、そのコード自体が起動しなければ使えない。
5. **個別の明示承認後だけ移行**：実装した書込み順はbootloader→env→firmware→overlay。純正U-Bootはsplit配置を起動できないが、OpenIPCのnor U-BootはカーネルのCRC不良を検出するとTFTP（`fwupd`）での復旧に入るので、先に書く。ただし、iBlackにEthernetがあるかは未確認で、TFTPの宛先も公開U-Bootの既定アドレスのままである。bootloaderを最後に書く順序は比較用に計画へ残した（[`migrate-plan.json`](../research/openipc-migrate/results/migrate-plan.json)）。どちらの順序でも原子的な更新ではなく、途中で電源が切れると起動できない期間がある。新U-Bootがこの基板で起動しなければ、復旧手段はSPIだけになる。rehearsal（読取り専用）を通してから電源を入れ直し、書込みへ進む。

移行後の通常更新はOpenIPCの対応 `sysupgrade -k -r` またはWeb UIを候補とし、設定はoverlayへ置き、意図しない `-n` を避ける。これは初回の純正→OpenIPC移行とは別工程である。[^https://github.com/OpenIPC/wiki/blob/master/en/nor-split-layout.md]

## 関連資料

- [再現手順と安全範囲](../research/openipc-port/README.md)
- [オンライン移行の実装と検証](../research/openipc-migrate/README.md)
- [既存の純正更新形式解析](FIRMWARE-SELF-UPDATE.md)
- [OpenIPC split NOR仕様](https://github.com/OpenIPC/wiki/blob/master/en/nor-split-layout.md)
- [EV100 U-Boot関連の既存課題](https://github.com/OpenIPC/firmware/issues/1542)
- [EV100基板依存の既存課題](https://github.com/OpenIPC/firmware/issues/2593)
