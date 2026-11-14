# OpenIPCオンライン移行：実装とオフライン検証

判断と実機上の未解決事項は [`docs/OPENIPC-PORTING.md`](../../docs/OPENIPC-PORTING.md)、フラッシュ配置と純正更新経路の検証は [`research/openipc-port`](../openipc-port/README.md) にある。

**このディレクトリのコードは、まだ実機で一度も実行していない。** この段階ではカメラへ接続しておらず、フラッシュの書込み・消去も再起動もしていない。全結果で `flash_write_allowed` は `false`。実機で実行するのは、下記の前提がそろい、所有者が別途明示的に承認した場合に限る。

## 実装したもの

純正iBlack（Hi3518EV100、NOR 8 MiB）を、UARTやSPIライターを使わずにLAN上のホストからOpenIPC `hi3518ev100-nor-lite-split` へ移行するための一式。

| ファイル | 役割 | 実行場所 |
| --- | --- | --- |
| `migrate_plan.py` | 公開OpenIPC firmwareとU-Bootの構造を検査し、絶対アドレスの書込み計画と純正MTDへの対応表を出す（dry-run） | ホスト |
| `make_env.py` | U-Bootの既定環境に、許可したキー（`wlandev`、Wi-Fi設定など）だけを足した64 KiBのenv画像を私的ディレクトリへ作る | ホスト |
| `build_migration_appfs.py` | 純正appfsに `openipc-migrate/{stage2.sh,migrate.conf}` と起動行を加えた移行appfsと、LAN配布用bundleを作る | ホスト |
| `serve_migration.py` | bundleの中身だけを配るLAN側HTTPサーバ。モードは起動時に固定（`standby`／`abort`／`rehearse`／`write`） | ホスト |
| `camera/stage2.sh` | カメラ側ドライバ。phase1は純正sh、phase2はRAM上のOpenIPC BusyBoxで動く | カメラ |
| `camera/migrate_hook.sh` | 純正アプリの停止・再開、mdat退避の送信、進捗通知 | カメラ（phase2） |
| `installer/openipc_migrate.sh` | フラッシュ8 MiB全体を書き換える本体。既定はdry-run | カメラ（phase2） |
| `validate_online_qemu.py` | 実ARMバイナリを `qemu-arm` で動かす検証 | ホスト |
| `tests/` | ユニット、E2E、インストーラーのシミュレーション | ホスト |

**移行appfsをカメラへ届ける部分（純正TCP3508の `0x96` 更新）は実装していない。** 既存の `research/sysupdate-offline/pack_update.py` のガードも変えていない。`0x96` はappfsとbakfsを両方書き換えたうえで再起動するため、bakfsを復旧先として残せない（[前段の検証](../openipc-port/README.md)）。生成されるappfsは、その経路で検証済みの上限に収まる大きさに抑えてある。

## 流れ

```text
公開firmware / U-Boot ─┬─ make_env.py ─────────────→ 私的env.img（Wi-Fi設定入り）
                      └─ build_migration_appfs.py ─→ 私的bundle（移行appfs、配布物、migrate.conf）
                                                        │
               （未実装）0x96で移行appfsを届ける ←────────┤
                                                        └→ serve_migration.py（LANホスト）

カメラ起動 → runapp.sh末尾の起動行 → 45秒後に stage2.sh phase1（純正sh、バックグラウンド）
  phase1: manifestを20秒ごと最大90回（30分）確認。GOが来なければ何もせず終わり、純正アプリはそのまま動く
          GOなら空きRAMを確認 → busybox・ld-musl.so・installer・hookを /dev/om へ取得
          → 純正 sha1sum と md5sum の両方で照合 → OpenIPC BusyBoxでphase2へexec
  phase2: 5ファイルをSHA-256で再照合 → firmware・U-Boot・envを取得してSHA-256照合
          → manifestを再確認（GOが取り消されていれば停止）→ installer（rehearsal または write）
```

## 運用順（実機で行う場合の想定。現時点では承認されていない）

1. `--mode standby`：カメラのポーリングが届くことだけを確かめる。何も配らない。
2. `--mode rehearse --backup-dir DIR`：読出しの安定性確認、mdat1／mdat2の私的退避とホストでの照合、書込み計画まで。フラッシュは書かず、純正アプリを再開して終わる。
3. 電源を入れ直す。phase1は起動時に一度だけ始まり、ロックも `/dev` 上に残るため、同じ起動中には再実行されない。
4. `--mode write --backup-dir DIR --write-confirm <WRITE_PHRASE>`：書き込む。`--reboot-confirm` がなければ再起動せず、状態を報告し続けながら電源断を待つ。

確認用の語句は `serve_migration.py` と `camera/stage2.sh`、インストーラーの定数で、一致しないとビルドもテストも通らない。

## 安全ゲート

- **run id**：既定ではビルドごとに128ビットの乱数を作り、appfsに焼き込む。run idが合わない要求には403を返して件数を数えるだけで、manifestも配布物も返さない。配布物を出すのは `rehearse` と `write` のときだけ。
- **語句**：phase1はmanifestに正確なGO語句がなければ何もしない。書込みにはさらにWRITE語句（サーバ側は `--write-confirm` が必須）、再起動にはREBOOT語句が要る。
- **ピン**：純正BusyBoxには `sha256sum` がないので、phase1はSHA-1とMD5の両方で照合する。phase2はSHA-256で全ファイルと配布物を照合する。サーバも起動時に `bundle.json` とappfs内の `migrate.conf` の両方と照合し、メモリ上の内容だけを配る。
- **RAMと実行環境**：空きRAMの推定値が12 MiB以上、`/dev/om` の空きが10 MiB以上でなければ始めない。phase2は必要なアプレットがそろっているかも確かめる。
- **停止とwatchdog**：書込み前に `cmsMain.out` と `SysUpdate` をSIGSTOPで止める。watchdogデバイスがあれば停止を拒否する（止めたアプリはwatchdogに給餌できないため）。
- **退避**：mdat1／mdat2を読み出し、base64でホストへ送り、ホストが返したSHA-256が手元と一致するまで書込みに進まない。
- **書込み**：bootloader → env → firmware → overlayの順で、この4領域が8 MiB全体を覆う。64 KiBごとに読み戻して多数決で照合し、最後に全体を照合する。
- **終了コード**：0は完了、1は何も書かずに中止（純正アプリを再開）、2は書込み途中の状態（再起動しない）、3は検証済みだが許可された再起動が起きなかった。
- **書込み後は終了しない**：書き換えた後のstage2は終了せず、報告を続ける。終了すると親が純正initへ付け替えられ、その実体はもう上書き済みのフラッシュにあるため。
- テスト用のキー（`OM_SIM_*` など）は `render_conf(allow_test_keys=True)` のときだけ受け付け、ビルダーはappfsに書かない。

## 検証結果（オフライン・エミュレーションだけ）

結果ファイル：[`results/validation-summary.json`](results/validation-summary.json)、[`results/online-qemu-validation.json`](results/online-qemu-validation.json)、[`results/migrate-plan.json`](results/migrate-plan.json)。

### x86ホスト

- `python3 -m unittest discover -s tests`：103件成功、スキップなし（計画27、env 14、サーバ18、ビルダー31、E2E 13）。
- `tests/simulate_install.py`：インストーラーのシミュレーション26件成功。
- E2Eでは、サーバ・stage2・hook・installerを実プロセスとして動かす。BusyBoxにはDebianの1.35.0を使い、phase1が使えるアプレットは純正の一覧に限る。書込み（env画像／env消去）、rehearsal、standbyとロック、abort、他のrun id、GO語句の欠落、RAM不足、runtimeのピン不一致、配布物のピン不一致、GOの取消し、退避送信の失敗、watchdogの13ケース。

### qemu-arm（ユーザーモード、`-cpu arm926`）

純正rootfsのuClibc版BusyBox 1.18.4と、OpenIPC firmware内のmusl版BusyBoxを動かした。通信は127.0.0.1だけで、書込み先はシミュレーション用のファイルだけ。

| 検査 | 内容 |
| --- | --- |
| `runtime_patch` | bundleのBusyBoxは、固定したOpenIPC版とPT_INTERPだけが違う |
| `runtime_on_stock_root` | 純正rootfsの隣で `/dev/om/ld-musl.so` を使って起動できる。未修正版は起動できない |
| `shells_parse` | 純正shが `stage2.sh`、`runapp.sh`、`migrate.conf` を解釈できる。OpenIPCのshが `stage2.sh`、installer、hookを解釈できる |
| `stock_applets` | 純正アプレットの一覧が、E2Eでphase1に許した一覧と一致する |
| `phase1_standby_stock` | 純正のshとアプレットでポーリングし、時間切れで終わる |
| `phase1_go_stock` | 純正のツールで実物のruntimeを取得・照合・配置する。ARMバイナリをx86ホストで実行できないため、そこで止まるのが期待どおり |
| `installer_write_sim` | OpenIPCのsh／BusyBoxで、停止、安定性確認、base64退避、4領域の書込み、最終照合 |
| `installer_rehearsal` | 同じ処理を読取り専用で行い、何も書かずにアプリを再開する |
| `full_chain_write` | phase1（純正sh）→ phase2（OpenIPC sh）→ installerとhookを1回で通す。x86で実行できるよう、配る `busybox` は実物を `qemu-arm` で起動するラッパーにした |
| `raw_post_truncates` | OpenIPCの `wget --post-file` は最初のNULで止まる（退避をbase64で送る理由） |

10検査すべてPASS（負荷の高いホストで約140秒）。rehearsalの後は純正アプリが再開し（`/proc` の状態 `S`）、書込みの後は止まったまま（`T`）になる。

### 実入力での私的ビルド

固定した公開OpenIPC firmware・U-Bootと、所有者が保管している純正appfsから作った。移行appfsは **2,613,248 B**。`0x96` 経路の上限2,621,432 Bに対して **8,184 B** の余裕がある。純正appfsの既存58項目のうち変更したのは `runapp.sh` だけで、`openipc-migrate/` 以下に3項目を追加した。圧縮条件も純正と同じ（xz／BCJ、所有者500/500）。bundleと画像はリポジトリの外に置き、コミットしていない。

### 検証で見つけて直した不具合

1. **退避の欠落**：OpenIPC 1.36.1の `wget --post-file` は内容を文字列として送り、最初のNULで切る。mdatにはNULが含まれるので、そのまま送るとホストには先頭しか届かない。hookはbase64で送り、ホストは復号後のSHA-256を返す。カメラ側はそれが手元の生ファイルと一致しなければ書込みに進まない。
2. **純正shで解釈できない**：純正BusyBox 1.18.4のshは算術展開 `$((...))` なしでビルドされている。しかも、最後の `case` に着く前にファイル内の関数をすべて解析する。phase2の関数にあった `$((...))` のせいで、phase1が始まる前に構文エラーで終わっていた（安全側に倒れるが、移行は始まらない）。カウンタを書き直し、ビルダーはファイル全体で `$((...))` を拒否するようにした。x86のE2Eでは見つからず、qemuで実物の純正shを動かして初めて分かった。

## 未検証のことと実機の前に必要なこと

- `0x96` の送信は未実装。appfsとbakfsを両方書き換えて再起動するので、それ自体に別の承認が要る。
- 既存bakfsの読出し不一致は未解決（[調査](../../docs/BAKFS-READBACK-INVESTIGATION.md)）。
- OpenIPC U-Bootはこの基板で起動したことがない。muslのruntimeも実機では動かしていない（qemuのユーザーモードだけ）。
- 実機の空きRAM（12 MiB必要）、`/dev` のtmpfs容量、watchdogデバイスの有無を測っていない。watchdogがあれば今のhookは停止を拒否するので、引継ぎの設計が別に要る。
- AR0141センサーとMT7601 Wi-Fiは、OpenIPC上で未確認。Wi-Fiで戻れなければ、Ethernet・TFTPでの復旧も未確認。
- 新U-BootのTFTP復旧（`fwupd`）は、公開U-Bootに焼き込まれた既定の `ipaddr`／`serverip` を使う。`make_env.py` はこの2つを変えないので、Ethernetがあったとしても、その既定アドレスに合わせたTFTPサーバが要る。
- 初号機はUARTとSPIライターを用意してから書くのが安全。書込み中に電源が切れると、旧firmwareも新firmwareも起動できない期間がある。
- rehearsalからwriteへ進む前に電源を入れ直す必要がある。

## 再実行

`tests/` と `validate_online_qemu.py` はカメラにもフラッシュにも触れない。

```sh
export PYTHONDONTWRITEBYTECODE=1
sha256sum -c research/openipc-migrate/SHA256SUMS   # リポジトリのルートで
cd research/openipc-migrate
# mksquashfs/unsquashfsがあれば、ビルダーの実ツール試験も動く（無ければスキップ）
OM_MKSQUASHFS="$HOST_MKSQUASHFS" OM_UNSQUASHFS="$HOST_UNSQUASHFS" python3 -m unittest discover -s tests -v
python3 tests/simulate_install.py

out=$(mktemp -d)
python3 migrate_plan.py --firmware "$PUBLIC_ARTIFACTS/firmware.bin.hi3518ev100" \
  --uboot "$PUBLIC_ARTIFACTS/u-boot-hi3518ev100-nor.bin" --env-mode image --output "$out/migrate-plan.json"

# 私的な入出力はリポジトリの外。wifi.varsは0600のkey=valueファイル（wlanssid、wlanpass）
python3 make_env.py --uboot "$PUBLIC_ARTIFACTS/u-boot-hi3518ev100-nor.bin" \
  --private-vars "$PRIVATE/wifi.vars" --output-dir "$PRIVATE/env"
python3 build_migration_appfs.py --firmware "$PUBLIC_ARTIFACTS/firmware.bin.hi3518ev100" \
  --uboot "$PUBLIC_ARTIFACTS/u-boot-hi3518ev100-nor.bin" --env-image "$PRIVATE/env/env.img" \
  --stock-appfs "$PRIVATE_INPUTS/stock-appfs.part" --host 192.0.2.10 \
  --mksquashfs "$HOST_MKSQUASHFS" --unsquashfs "$HOST_UNSQUASHFS" --out "$PRIVATE/bundle"

# qemu-armとunsquashfsが動的リンクなら、必要なLD_LIBRARY_PATHを --qemu-lib-path かラッパーで渡す
python3 validate_online_qemu.py --bundle "$PRIVATE/bundle" --stock-rootfs "$PRIVATE_INPUTS/stock-rootfs" \
  --qemu-arm "$QEMU_ARM" --qemu-lib-path "$QEMU_LIBS" --unsquashfs "$HOST_UNSQUASHFS" \
  --out-json "$out/online-qemu-validation.json"
```

`192.0.2.10` は説明用のアドレス。実際のLANアドレスはGitに書かない。`serve_migration.py` は実際にカメラの相手をするサーバなので、上の再実行には含めていない。使い方は `python3 serve_migration.py --help` と上の運用順のとおり。

## Gitに入れないもの

bundle、移行appfs、env画像、firmware・U-Bootの実体、純正appfs・rootfs、mdatの退避、run id、LANアドレス、SSID・パスフレーズ、私的ログ、更新envelope。`.gitignore` だけに頼らず、追加するファイルを明示的に選んで中身を確かめる。
