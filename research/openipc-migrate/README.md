# OpenIPCオンライン移行（純正U-Bootを残す方式）：実装とオフライン検証

判断と実機上の未解決事項は [`docs/OPENIPC-PORTING.md`](../../docs/OPENIPC-PORTING.md)、純正更新経路の前段検証は [`research/openipc-port`](../openipc-port/README.md) にある。

**このディレクトリのコードは、まだ実機で一度も実行していない。** この段階ではカメラへ接続しておらず、フラッシュの書込み・消去も再起動もしていない。全結果で `flash_write_allowed` は `false`。実機で動かすのは、所有者が段階ごとに明示的に承認した場合に限る。

## 前提と方式

対象の個体はWi-Fiしか使えない（UARTもEthernetもない）。SPIでの読み書きはでき、8 MiB全体のダンプがある。この前提で次のように決めた。

- **純正U-Boot（`0x000000`–`0x03FFFF`）は一度も書かない。** この基板で起動したことのないOpenIPC U-Bootを焼くと、起動しなかったときにSPI以外で戻る手段がない。純正U-Boot 2010.06は、今までどおり自分のenv（`0x040000`、128 KiB）を読む。そのenvの `bootcmd` でOpenIPCのkernelを起動する。
- OpenIPCは純正U-Bootの後ろにずらして置く（下の配置）。公開firmware `hi3518ev100-nor-lite-split`（`firmware.bin`、7,012,352 B）は、加工せずに `0x070000` へ書く。
- 本番の前に、**自動で純正へ戻る1回だけの試験起動**を行う。OpenIPCのkernelを純正bakfs領域から一度だけ起動し、純正のユーザーランドのままOpenIPCのWi-Fiドライバで接続して、LAN上のホストへ報告する。試験用envは、kernelを起動する**前に**純正の `bootcmd` を書き戻して `saveenv` する。kernelのCRC不良、panic、停止後の電源入れ直しのどれでも、次の起動は純正になる。
- その前に、純正U-Bootがこちらの用意したenvを実際に実行し、`saveenv` できることをprobeで確かめる。
- 最初に読取り専用のdiagnoseを行う。128ブロック全部を3回ずつ読んで安定性を見て、純正U-BootがSPIダンプと一致することも確かめる。

以前の実装（OpenIPC U-Bootを含めて8 MiB全体を書き換える方式、コミット `b66cf56`）は、UARTのないこの個体には向かないので削除した。`installer/openipc_migrate.sh`、`migrate_plan.py`、`tests/simulate_install.py`、`tests/test_migrate_plan.py`、`results/migrate-plan.json` が該当する。履歴には残っている。

## フラッシュ配置と容量

```text
純正          0x000000 2048K boot（U-Boot 256K、env 128K、kernel）  0x200000 768K rootfs  0x2C0000 2560K appfs
              0x540000 128K mdat1  0x560000 128K mdat2  0x580000 2560K bakfs
試験起動      純正の6区画（すべて読取り専用）に加えて
              tk 0x580000 1856K（OpenIPC kernel）  tp 0x750000 704K（試験用SquashFS）
              tl 0x070000 64K（試験ログ。純正では未使用の0xFF領域）  senv 0x040000 128K（純正env）
移行後        0x000000 384K boot(ro)（純正U-Boot＋純正env）  0x060000 64K env（OpenIPCのfw_printenv用）
              0x070000 1856K kernel  0x240000 4992K rootfs  0x720000 896K rootfs_data
```

移行後の `mtdparts` は `hi_sfc:384k(boot)ro,64k(env),1856k(kernel),4992k(rootfs),896k(rootfs_data)` で、合計8 MiB。`0x060000`–`0x07FFFF` は純正の配置では使われていない。

固定した公開firmwareと所有者保管の入力で測った値（[`results/stockboot-plan.json`](results/stockboot-plan.json)）：

| 項目 | 使用 | 上限 | 余裕 |
| --- | --- | --- | --- |
| kernel（試験と本番で同じuImage） | 1,864,732 B | 1,900,544 B（1856 KiB） | 35,812 B |
| OpenIPC rootfs（SquashFS） | 5,100,346 B | 5,111,808 B（4992 KiB） | 11,462 B |
| 試験用SquashFS（cfg80211.ko、mt7601sta.ko、`.dat`、env、tlの雛形、スクリプト） | 520,192 B | 720,896 B（704 KiB） | 200,704 B |
| 移行appfs（`0x96` 経路） | 2,613,248 B | 2,621,432 B | 8,184 B |
| 試験用 `bootcmd`（純正U-Bootの `CONFIG_SYS_CBSIZE` 1024） | 417文字 | 1,023文字 | 606文字 |
| probe用 `bootcmd` | 158文字 | 1,023文字 | 865文字 |
| 移行後の `bootcmd`／`bootargs` | 69文字／174文字 | 1,023文字 | — |

試験用SquashFSは、SSID 32文字・パスフレーズ63文字（WPAの最大長）の仮の値で測った。kernelとrootfsの余裕が小さいので、将来の更新で別のfirmwareを書く場合は、同じ容量検査を必ず通す。

前段で読出し不一致が出た `0x6c4f28` は、純正ではbakfs+`0x144f28`、試験起動ではtk（OpenIPC kernel）+`0x144f28`、移行後はrootfs+`0x484f28`（使用中のSquashFSデータ内）に当たる。diagnoseはこのブロックを16回読み、1回でも違えば先へ進まない。試験起動は `verify=y` で起動するので、kernelをこの位置で読み違えればCRC不良で純正へ戻る。

## 流れ

```text
（未実装・要承認）純正TCP3508の 0x96 で移行appfsを届ける：appfsとbakfsを書き換えて再起動
                                  ↓
純正firmwareが起動 → runapp.sh末尾の起動行 → stage2.sh phase1（純正sh）がホストのmanifestを確認
                                  ↓ run id、GO語句
phase2（RAM上のOpenIPC BusyBox）：manifestのseqが変わるたびに om_stockboot.sh を1回だけ実行
  1. diagnose     読取りのみ         → build_stockboot.py diag（SPIダンプと照合）
  2. probe-arm    純正env ← probe env、再起動 → 純正U-Bootが純正bootcmdを書き戻してsaveenv → 純正で起動
                  → collect          → build_stockboot.py collect --stage probe
  3. trial-arm    bakfs ← 試験イメージ、最後に純正env ← 試験用env、再起動
                  → 純正U-BootがsaveenvしてからOpenIPC kernelを一度だけ起動
                  → trial.sh（純正ユーザーランド）がWi-Fiで接続してホストへ報告 → 再起動で純正へ
                  → collect          → build_stockboot.py collect --stage trial
  4. final        OpenIPC env → firmware → overlay消去 → 最後に純正env、再起動でOpenIPC
  （final前なら restore-env で、diagnose時に読んだ純正envへいつでも戻せる）
```

| ファイル | 役割 | 実行場所 |
| --- | --- | --- |
| `stock_layout.py` | 配置、純正env（128 KiB）の符号化、純正U-Boot 2010.06のコマンド解釈器とLinux 3.0.8 `cmdlinepart` のモデル、probe／試験／本番／復元envの生成と検査 | ホスト |
| `build_stockboot.py` | ホスト側の状態機械。diagnoseとcollectの結果を検査し、各段階の私的なaction一式を作る。前の段階が通っていなければ拒否する。`plan` は容量報告 | ホスト |
| `make_env.py` | OpenIPC U-Bootの既定envから、移行後の `0x060000` に置く64 KiBのenv画像（Wi-Fi設定入り）を作る | ホスト |
| `build_migration_appfs.py` | 純正appfsに `openipc-migrate/{stage2.sh,migrate.conf}`、起動行、試験起動の分岐を加えた移行appfsとbundleを作る | ホスト |
| `serve_migration.py` | bundleとaction一式だけを配るLAN側HTTPサーバ。1プロセス1モード | ホスト |
| `camera/stage2.sh` | カメラ側ドライバ。phase1は純正sh、phase2はRAM上のOpenIPC BusyBox | カメラ |
| `installer/om_stockboot.sh` | diagnose、collect、probe-arm、trial-arm、restore-env、finalの本体 | カメラ（phase2） |
| `camera/trial.sh`、`camera/trial_dhcp.sh` | 試験起動でのWi-Fi接続、DHCP、報告、`tl` へのログ記録 | カメラ（試験起動） |
| `camera/migrate_hook.sh` | 純正アプリの停止・再開、送信、進捗 | カメラ（phase2） |
| `validate_online_qemu.py` | 実ARMバイナリを `qemu-arm` で動かす検証 | ホスト |
| `tests/` | ユニット、模擬フラッシュでのインストーラー、E2E | ホスト |

**移行appfsを届ける部分（純正TCP3508の `0x96` 更新）は実装していない。** 既存の `research/sysupdate-offline/pack_update.py` のガードも変えていない。`0x96` はappfsとbakfsを両方書き換えて再起動するので、それ自体に別の承認が要る。trial-armの後はbakfsに純正appfsの控えが残らない。そこから先の復旧手段はSPIダンプになる。

## 運用順（実機で行う場合の想定。現時点では承認されていない）

各段階で、ホストは1モードだけの `serve_migration.py` を起動する。カメラは起動ごとにphase1を1回だけ始める（起動45秒後から20秒ごと、最大30分）。間に合わなければ電源を入れ直す。私的なディレクトリ（`$ST` 状態、`$UP` 受信物、`$A` action一式）はリポジトリの外に置き、0700にする。

```sh
python3 serve_migration.py --bundle "$B" --mode standby                       # 届くことだけ確認
python3 serve_migration.py --bundle "$B" --mode diagnose --backup-dir "$UP"   # 読取りのみ
python3 build_stockboot.py --state "$ST" diag --uploads "$UP" --spi-dump "$SPI_DUMP"
python3 build_stockboot.py --state "$ST" probe --out "$A/probe"
python3 serve_migration.py --bundle "$B" --mode probe-arm --action-dir "$A/probe" --backup-dir "$UP" \
  --write-confirm yes-arm-saveenv-probe --reboot-confirm yes-reboot-for-saveenv-probe
python3 serve_migration.py --bundle "$B" --mode collect --backup-dir "$UP"
python3 build_stockboot.py --state "$ST" collect --uploads "$UP" --stage probe
python3 build_stockboot.py --state "$ST" trial --firmware "$FW" --oipc-rootfs "$OIPC_ROOTFS" \
  --wifi-vars "$PRIVATE/wifi.vars" --mksquashfs "$HOST_MKSQUASHFS" --out "$A/trial"
python3 serve_migration.py --bundle "$B" --mode trial-arm --action-dir "$A/trial" --backup-dir "$UP" \
  --write-confirm yes-arm-one-shot-openipc-trial --reboot-confirm yes-reboot-into-openipc-trial
#   （試験起動の報告もこのサーバが受ける。純正へ戻るまで止めない）
python3 serve_migration.py --bundle "$B" --mode collect --backup-dir "$UP"
python3 build_stockboot.py --state "$ST" collect --uploads "$UP" --stage trial
python3 build_stockboot.py --state "$ST" final --firmware "$FW" --out "$A/final"
python3 serve_migration.py --bundle "$B" --mode final --action-dir "$A/final" --backup-dir "$UP" \
  --write-confirm yes-migrate-this-camera-to-openipc --reboot-confirm yes-reboot-into-openipc
```

途中でやめるときは `build_stockboot.py restore` と `serve_migration.py --mode restore-env --write-confirm yes-restore-stock-uboot-env` で、diagnose時の純正envへ戻す。

移行後は、OpenIPCの `sysupgrade -u`（U-Boot更新）を使わない。`boot` 区画は読取り専用にしてあるが、この区画には純正U-Bootと純正envがあり、ここを書くと起動しなくなる。kernelとrootfsの更新も、上の容量に収まるものに限る。OpenIPC側の `fw_setenv` は `0x060000` のenvを書くだけで、純正U-Bootの起動設定は変わらない。

## 安全ゲート

- **run idと語句**：run idは既定でビルドごとに128ビットの乱数を作り、appfsに焼き込む。run idが合わない要求には何も配らない。phase1はmanifestに正確なGO語句がなければ何もしない。書込みには段階ごとの書込み語句（サーバ側は `--write-confirm`）が要り、再起動にはさらに再起動語句（`--reboot-confirm`）が要る。
- **1回だけ**：サーバは各actionを乱数のseqで1回だけ出し、カメラが開始を報告したら取り下げる。
- **ピン**：phase1は純正の `sha1sum` と `md5sum` の両方で照合し、phase2はSHA-256で照合する。action一式はmanifestが示すSHA-256で取得する。`firmware.bin` と `env-openipc.img` は、`migrate.conf` に焼き込んだピンでしか取得しない。サーバも起動時に全ファイルを再ハッシュし、action一式を `stock_layout.py` で検査し直してから、メモリ上の内容だけを配る。
- **ホスト側の順序**：`build_stockboot.py` は前の段階が通っていなければ次を作らない。diagnoseは、全ブロックの読出しが安定していること、16回読んだ弱いブロックが一致すること、純正U-BootがSPIダンプと一致すること、純正envが純正どおりであること、MTD表が純正の6区画であること、MT7601（`148f:7601`）があること、watchdogデバイスがないことを確かめる。試験起動は、nonce、マーカー付きの起動、MTD表、モジュールのロード、Wi-Fiインターフェース、DHCP、ホストへの到達、U-Bootの `saveenv`、純正env、U-Bootの不変、読出しの安定をすべて確かめる。
- **書く前の照合**：書込みactionごとに、純正U-BootのSHA-256がdiagnose時の値と一致することを、書く前と書いた後に確かめる。純正envが、ホストがactionを作ったときのenvと一致しなければ書かない。
- **書込みと照合**：mtd1–mtd5は区画全体を消去してcharデバイスへ書く。mtd0（env）は64 KiBごとに `mtdblock0` 経由で書き、ほかのプロセスが `mtdblock0` を開いていないときだけ書く。照合は、ページキャッシュを避けるためMTDのcharデバイスを最大3回読み、2回一致で判定する。
- **書く順序**：trial-armはbakfs、最後に純正env。finalは `0x060000` のenvと `0x070000` の先頭ブロック（ここまでは純正が使わない領域で、中止しても純正のまま）、次に `0x080000` 以降（ここから後戻りできない）、最後に純正env。後戻りできない点を越えたら中止せず、失敗したブロックを再試行して記録し、純正envは必ず最後に書く。その後の実行で再試行できる。
- **試験起動の分岐**：runapp.shの分岐は、`/proc/cmdline` に ` omtrial=1` があり、`/proc/mtd` に試験用の `tp` 行が正確にある場合だけ動く。分岐に入るとすぐ900秒のタイマーを起動し、時間が来たら `reboot -f` する。trial.shが書くのは `tl`（ログ）だけで、`saveenv` が失敗していた場合に限り純正envを書き戻す。Wi-Fiの設定は記録しない。
- **停止とwatchdog**：書く前に `cmsMain.out` と `SysUpdate` をSIGSTOPで止める。watchdogデバイスがあれば停止を拒否する。空きRAMの推定値が12 MiB以上、`/dev/om` の空きが10 MiB以上でなければ始めない。
- **終了コード**：0は完了、1は純正U-Bootの起動対象を変えずに拒否・失敗、2は書込み途中で照合が終わっていない（電源を保ったままfinalを再試行するか、SPIで復旧）、3は照合済みだが許可された再起動が起きなかった。finalで後戻りできない点を越えた後は、stage2が終了せず、finalの再試行とcollectだけを受け付ける。
- テスト用のキー（`OM_SIM_*` など）は `render_conf(allow_test_keys=True)` のときだけ受け付ける。ビルダーはappfsに書かない。

## 電源断・失敗時

| 段階 | 途中で電源が切れたとき |
| --- | --- |
| probe-arm：env書込み（64 KiB×2） | envのCRCが不良 → 純正U-Bootの既定env（`mem=40M`、純正配置）→ 純正で起動 |
| probe起動：U-Bootの `saveenv` | 同上 |
| trial-arm：bakfs書込み | 純正で起動（bakfsには試験イメージしかない） |
| trial-arm：env書込み | envのCRCが不良 → 既定env → 純正で起動 |
| 試験起動：U-Bootの `saveenv` | 同上 |
| 試験kernelのCRC不良、panic、停止して電源入れ直し | 純正bootcmdは保存済み → 純正で起動 |
| final：`0x060000` のenvと `0x070000` の先頭ブロック | 純正のまま（純正が使わない領域） |
| final：`0x080000` 以降のfirmwareとoverlay消去 | 純正kernelが消え、envは純正のまま → U-Bootが止まる → SPIで復旧 |
| final：純正envの書込み（最後） | envのCRCが不良 → 既定env → 純正bootcmdがOpenIPCのデータを読む → SPIで復旧 |
| final完了後 | OpenIPCで起動（`verify=y`。CRC不良ならリセットを繰り返す → SPIで復旧） |

finalの後戻りできない区間は、firmware約6.6 MiBの消去・書込み・照合の間（数分の見込み、実測していない）。この間は電源を切らない。

## 検証結果（オフライン・エミュレーションだけ）

結果ファイル：[`results/validation-summary.json`](results/validation-summary.json)、[`results/online-qemu-validation.json`](results/online-qemu-validation.json)、[`results/stockboot-plan.json`](results/stockboot-plan.json)。

### x86ホスト

`python3 -m unittest discover -s tests -v`：**135件が成功**（失敗0、スキップなし、実ツールの `mksquashfs`／`unsquashfs` あり、約6分）。

| モジュール | 件数 | 内容 |
| --- | --- | --- |
| `test_stock_layout` | 21 | 配置と容量、純正env（128 KiB）の符号化、純正U-Bootのコマンド解釈と `_do_setenv`、`cmdlinepart`、probe／試験／本番／復元env、`0x050000` にuImageヘッダがないこと |
| `test_build_stockboot` | 14 | 段階の順序、diagnoseとcollectの判定（不安定なブロック、16回読む弱いブロック、別個体のダンプ）、試験用SquashFSの容量とtlの雛形、純正BusyBoxで動かないスクリプトを試験用SquashFSに入れないこと、action一式 |
| `test_om_stockboot` | 12 | 模擬フラッシュ上の `om_stockboot.sh`：diagnoseとcollectは読取りのみ、probe-armは純正envだけを書く、語句と計画どおりのenvが要る、env書込みの失敗で元に戻す、試験イメージの失敗ではenvを書かない、finalの一時的な読み書き不良・後戻りできない点の前後の失敗・再試行 |
| `test_build_migration_appfs` | 37 | 移行appfsの組立てと容量、runapp.shの起動行と試験起動の分岐（純正shで実行）、stage2.shと `trial.sh` が純正BusyBoxの範囲に収まること（`dd` の引数、`grep -x` を含む）、bundle |
| `test_serve_migration` | 23 | bundleとaction一式の検査、モードごとのmanifest、1回だけのaction、run id、受信物（純正 `wget --post-data` の%XX形式の試験ログを元に戻すこと、受信を断るときも本文を読み切ってから応答すること）、語句の一致 |
| `test_make_env` | 16 | OpenIPC U-Bootの既定env抽出、許可キー、`0x060000` 用の配置、私的ファイルの扱い |
| `test_e2e_online` | 12 | phase1→phase2→installer→試験起動の `trial.sh` をHTTP越しに通す（Debian版BusyBox。`dd` と `grep` は純正と同じ引数だけ通し、`wget --post-data` は純正と同じ%XX形式で送る。模擬フラッシュ）。`tl` の中身、ホストが受け取った試験ログ、`saveenv` が失敗していた場合の純正envの書き戻しも確かめる |

### qemu-arm（ユーザーモード、`-cpu arm926`）

純正rootfsのuClibc版BusyBox 1.18.4と、OpenIPC firmware内のmusl版BusyBox 1.36.1を動かした。通信は127.0.0.1だけ。フラッシュは模擬ファイルだけで、`/dev/mtd*` は開いていない。

7検査すべて成功（合計約4分）。

| 検査 | 結果 | 秒 | 内容 |
| --- | --- | --- | --- |
| `runtime_patch` | PASS | — | OpenIPCのmusl BusyBoxと `ld-musl` を、interpreterのパスだけ書き換えて `/dev/om` から動かせる |
| `runtime_on_stock_root` | PASS | — | 純正rootfsの上で、書き換えたBusyBoxが必要なアプレットをすべて実行できる |
| `shells_parse` | PASS | — | `stage2.sh` を純正BusyBox 1.18.4のshで、`om_stockboot.sh`・`migrate_hook.sh`・`trial.sh`・`trial_dhcp.sh` をそれぞれ実行するshで構文検査 |
| `stock_applets` | PASS | — | 純正BusyBoxのアプレット一覧がE2Eの許可リストと一致すること。純正 `dd` が `conv=` などを拒み（終了コード1）`if/of/bs/count/skip/seek` は通すこと、`grep` に `-x` がなく行頭と行末の固定は効くこと、純正shの `1<>` でtlの雛形にログを重ねても残り（`OMTLOG-END` と0埋め）が壊れないこと、`wget --post-data` が本文を%XX形式（`application/x-www-form-urlencoded`）で送り、それが `tests/om_testlib.py` の模型と一致し、`serve_migration.py` が元のログに戻せること、E2Eで模擬・禁止にしている `udhcpc`・`mount` と `wget` に `trial.sh` が渡す引数を解釈できること（空のユーザー名前空間の中で） |
| `etc_overlay_mount` | PASS | — | 試験起動で `/etc` の上に設定を重ねる方法（bind mount）が純正のshとBusyBoxで動く |
| `runapp_trial_branch` | PASS | 17 | 移行appfsの `runapp.sh` を純正shで実行：通常起動、試験起動、ペイロードなしの即再起動、追加行の範囲 |
| `e2e_real_binaries` | PASS | 227 | E2Eの12件を、純正BusyBox（phase1）とOpenIPC BusyBox（phase2、installer）の実物で通す |

### 検証で見つけて直した不具合

1. **env終端の後ろの残骸**：純正U-Boot 2010.06の `_do_setenv` は、envを詰め直しても終端より後ろを消さない。試験起動の後のenvには、終端の後ろに `omtrial=1` 入りの古い `bootcmd` が残る。`om_stockboot.sh` はこれも読んでしまい、純正に戻ったenvを「試験用のまま」と誤判定していた。終端（最初の空行）で読むのをやめるよう直した。模擬U-Bootを `_do_setenv` と1バイト単位で同じ動作にし、回帰テストを足した。
2. **純正BusyBoxで動かない書き方（3件）**：(a) 純正BusyBox 1.18.4の `dd` は `if=`・`of=`・`bs=`・`count=`・`skip=`・`seek=` しか受け付けず、`conv=`・`ibs=`・`obs=`・`iflag=`・`oflag=` を渡すと何もせずに終了コード1で終わる。`trial.sh` は `conv=sync`／`conv=notrunc` を使っていたので、実機の試験起動では `tl` へのログが一度も書かれず、`saveenv` が失敗していた場合の純正envの書き戻しも失敗するところだった（書き戻せなければ試験起動を繰り返し、SPIでの復旧が要る）。ホストが作る64 KiBの雛形（`tl-blank.img`：0埋めと `OMTLOG-END`）にログを `1<>` で重ね書きし、`tl` と `senv` は区画全体を `conv=` なしで書くよう直した。(b) 純正の `grep` には `-x` がない。`trial.sh` はmanifestの照合に `grep -qx` を使っていたので、ホストに届いても必ず `host=fail` と報告し、finalへ進めなくなるところだった。行頭と行末を固定した `grep -q` に直した。(c) 純正の `wget --post-data` は、本文の英数字と `!&'()*-.=_~` 以外をすべて%XXにして送る（`application/x-www-form-urlencoded`）。`serve_migration.py` は試験ログを届いたまま保存していたので、`build_stockboot.py collect` がフラグを1つも読めず、試験起動が成功していてもfinalへ進めなくなるところだった。`serve_migration.py` がこの形式を見分けて元に戻すよう直した（ログには必ず空白と改行があり、%XX形式の本文には現れないので取り違えない）。3件ともx86の試験はDebian版BusyBoxで通っていて、qemu上の実物で通したE2Eで分かった。純正shで動くスクリプトはビルド時に (a)(b) を検査し、x86のE2Eの `dd` と `grep` は純正と同じものしか通さず、`wget` は純正と同じ形式で送るようにした。qemuの検査には、純正の `dd`・`grep`・`wget --post-data` の挙動、`1<>` での重ね書き、E2Eで模擬・禁止にしている `udhcpc`・`mount` と `wget` の引数解釈（空のユーザー名前空間の中で）を足し、E2Eには書き戻しの経路と、ホストが受け取った試験ログが `trial.sh` の書いたものと一致することの確認を足した。
3. **受信を断るときの応答**：`serve_migration.py` は大きすぎる受信などを断るとき、本文を読まずに接続を閉じていた。送り手には応答の代わりに接続のリセットが届くことがあり、x86の試験がときどき失敗した。1 MiBまでは本文を読み捨ててから応答するよう直した。
4. **検証側の誤り3件**：runapp.shの試験分岐の照合文字列が1文字ずれていた。純正BusyBoxに `readlink` がないことを見落としていた。qemu上で遅い純正shに、ホスト向けの時間しきい値（1.5秒）を当てていて、負荷が高いと失敗した。時間で判定するのをやめ、起動行が待機に入ったことをファイルで確かめてから先へ進めるようにした。どれも製品側のコードではなく、検証側を直した。
5. **検証の後始末**：検証をSIGTERMで止めると、テスト・模擬サーバ・`qemu-arm` の子プロセスと一時ディレクトリが残っていた。子を専用のセッションで動かし、止めるときにまとめて終了させるようにした。
6. **足した検査（2件）**：(a) 弱いブロック（`0x6c4f28` を含む64 KiB）を16回読んだ結果が1回でも違えば、`build_stockboot.py diag` は先へ進まない。(b) OpenIPCのkernel（`hisfc350`）は起動時に `0x050000` だけを見て、uImageヘッダがあれば独自の区画表を登録し、`mtdparts=` と `root=` を無視する（kernelのパッチと文字列から確認。実機では未確認）。この方式では `0x050000` は純正envの中で、文字と0埋めしかない。試験用envと移行後envを作るときに、ここにuImageのmagicがないことを検査するようにした。
7. 以前の方式で見つけて直した2件（OpenIPCの `wget --post-file` が最初のNULで止まるのでmdatの退避をbase64で送る、純正shが `$((...))` なしでビルドされている）は、この方式にも引き継いでいる。

## 未検証のことと実機の前に必要なこと

- `0x96` の送信は未実装。appfsとbakfsを両方書き換えて再起動するので、それ自体に別の承認が要る。
- 純正U-Bootが、こちらのenvを実際に実行して `saveenv` できるか（probe）。純正U-Bootのコマンド解釈はバイナリから読み取ってモデル化しただけで、実機では動かしていない。
- OpenIPC kernelがこの基板で起動するか。SPIフラッシュ素子の認識、純正rootfs（xz）のマウント、`mtdparts` の解釈が分かるのは試験起動のときだけ。kernelがユーザーランドに着く前に止まった場合は、900秒のタイマーが動かないので、手で電源を入れ直す（純正に戻る）。
- MT7601を、OpenIPCのモジュールと純正ユーザーランドで接続できるか（qemuでは模擬）。移行後のOpenIPCはwpa_supplicantを使うので、その経路は試験起動では確かめられない。移行後にWi-Fiでつながらなければ、戻る手段はSPIだけになる。
- 既存bakfsの読出し不一致は未解決（[調査](../../docs/BAKFS-READBACK-INVESTIGATION.md)）。
- AR0141センサーは、OpenIPC上で未確認。
- 実機の空きRAM、`/dev` のtmpfs容量、watchdogデバイスの有無は、diagnoseで初めて分かる。
- 試験起動で `tl` と `senv` へ書く経路（`mtdblock7`／`mtdblock8` を開く `dd`）は、純正 `dd` と `1<>` の動作をqemuで確かめ、模擬ファイルに通しただけ。実機のMTDでの書込みは試験起動で初めて分かる。
- Wi-Fiの設定は、試験用SquashFS（xz圧縮、bakfs領域）と、移行後の `0x060000` のenv（OpenIPCの通常どおり平文）に入る。試験で止めた場合、bakfs領域に残る。

## 再実行

`tests/` と `validate_online_qemu.py` はカメラにもフラッシュにも触れない。

```sh
export PYTHONDONTWRITEBYTECODE=1
sha256sum -c research/openipc-migrate/SHA256SUMS   # リポジトリのルートで
cd research/openipc-migrate
# mksquashfs/unsquashfsがあれば、ビルダーと試験用SquashFSの実ツール試験も動く（無ければスキップ）
OM_MKSQUASHFS="$HOST_MKSQUASHFS" OM_UNSQUASHFS="$HOST_UNSQUASHFS" python3 -m unittest discover -s tests -v
python3 stock_layout.py plan --firmware "$PUBLIC_ARTIFACTS/firmware.bin.hi3518ev100"

# 私的な入出力はリポジトリの外。wifi.varsは0600のkey=valueファイル（wlanssid、wlanpass）
python3 make_env.py --uboot "$PUBLIC_ARTIFACTS/u-boot-hi3518ev100-nor.bin" \
  --private-vars "$PRIVATE/wifi.vars" --layout stockboot --output-dir "$PRIVATE/env"
python3 build_migration_appfs.py --firmware "$PUBLIC_ARTIFACTS/firmware.bin.hi3518ev100" \
  --oenv-image "$PRIVATE/env/env-openipc.img" --stock-appfs "$PRIVATE_INPUTS/stock-appfs.part" \
  --host 192.0.2.10 --mksquashfs "$HOST_MKSQUASHFS" --unsquashfs "$HOST_UNSQUASHFS" --out "$PRIVATE/bundle"
python3 build_stockboot.py plan --firmware "$PUBLIC_ARTIFACTS/firmware.bin.hi3518ev100" \
  --oipc-rootfs "$OIPC_ROOTFS" --mksquashfs "$HOST_MKSQUASHFS" --bundle "$PRIVATE/bundle"

# qemu-armが動的リンクなら、必要なLD_LIBRARY_PATHを --qemu-lib-path で渡す
python3 validate_online_qemu.py --bundle "$PRIVATE/bundle" --stock-rootfs "$PRIVATE_INPUTS/stock-rootfs" \
  --qemu-arm "$QEMU_ARM" --qemu-lib-path "$QEMU_LIBS" --unsquashfs "$HOST_UNSQUASHFS" \
  --mksquashfs "$HOST_MKSQUASHFS" --out-json "$out/online-qemu-validation.json"
```

`$OIPC_ROOTFS` は公開firmwareのrootfsを展開した木。`192.0.2.10` は説明用のアドレスで、実際のLANアドレスはGitに書かない。`serve_migration.py` は実際にカメラの相手をするサーバなので、上の再実行には含めていない。

## Gitに入れないもの

bundle、移行appfs、env画像、試験イメージと試験用SquashFS、action一式、`build_stockboot.py` の状態ディレクトリ、カメラからの受信物（diagnose、collect、mdat、ログ）、firmware・U-Bootの実体、純正appfs・rootfs、SPIダンプ、run id、LANアドレス、SSID・パスフレーズ、私的ログ、更新envelope。`.gitignore` だけに頼らず、追加するファイルを明示的に選んで中身を確かめる。
