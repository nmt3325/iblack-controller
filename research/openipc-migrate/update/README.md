# 移行後のOpenIPCの更新（update/）

ネットワーク経由で移したカメラは、純正U-Bootのまま固定の区画で起動する（[../README.md](../README.md)）。OpenIPC標準の更新はこの配置を前提にしていないので、そのまま使うと起動しなくなる。シリアル端子がないため、そうなると基板のSPIを直接読み書きするまで戻せない。ここには、壊す更新を止めるガードと、区画に収まる版かを調べる道具を置く。

| ファイル | 役割 | 確認状況 |
| --- | --- | --- |
| `sysupgrade-guard.sh` | カメラの `/usr/sbin/sysupgrade` を置き換え、区画に収まる更新だけを元の更新処理（`/rom/usr/sbin/sysupgrade`）に渡す | 2台目に導入済み。カメラ上で書き込みなしの判定28件（下記） |
| `check_update.py` | ホストで、更新パッケージが区画に収まるかを調べる。読むだけで、何も送らない | テスト9件。配布パッケージ2つで確認（下記） |

**版の入れ替え（kernel・rootfsの書き込み）は、まだ実機で一度も行っていない。** 下の「区画に収まる版の入れ方」は、判定と手順を確かめた段階のもの。

## 固定の区画

| 区画 | 開始 | 大きさ |
| --- | --- | --- |
| boot（純正U-Boot） | 0x000000 | 384K |
| env | 0x060000 | 64K |
| kernel | 0x070000 | 1856K（1,900,544 B） |
| rootfs | 0x240000 | 4992K（5,111,808 B） |
| rootfs_data（overlay） | 0x720000 | 896K |

純正U-Bootがbootargsで渡す配置なので、Linux側からは変えられない。U-Bootは書き換えない。

## 標準の更新で起きること

| 操作 | 起きること |
| --- | --- |
| Web UIのGitHubからの更新、`sysupgrade -k -r`、`--url`・`--channel`・`--build`、自動更新 | 分割版パッケージの `firmware.bin`（uImageの後にsquashfsを続けたもの）を取得し、0x070000から一続きに書く。いまの版（2.6.11.13 lite、`a0f341e`）では7,012,352 Bで偶然0x720000で終わるが、解析時点のnightly（`4cc6476`）は7,077,888 Bで0x730000まで届き、rootfsが区画からはみ出してrootfs_dataに食い込む。kernelが64 KiBの境目を越えて大きくなれば、rootfsの開始位置もずれる |
| nightlyの通常版パッケージ | rootfsが5,124,096 Bで、区画を12,288 B超える（kernelは1,864,788 Bで入る） |
| 更新処理の自己更新（`-z` なし） | 書き込む前に、更新処理そのものをOpenIPC masterの版に置き換える。中身を読んでいない版がフラッシュを書く |
| `sysupgrade -u` | U-Bootを書き換える |
| `-f`・`--force_*`・`--no_ramfs` | 更新処理自身の確認（チェックサム・SoC・版）を外す、または動作中のフラッシュをそのまま書く |

いまの版の通常版パッケージは入る（kernel 1,864,732 Bで余り35,812 B、rootfs 5,103,616 Bで余り8,192 B）。rootfsの余りが8 KiBしかないので、新しい版はたいてい入らない。区画に入るよう小さくビルドした版を用意する作業は未着手。

## 2つの層

1. **ガード**（`/usr/sbin/sysupgrade`）が主な防御。次のものだけを通し、ほかはすべて更新処理を起動する前に断って `logger -t sysupgrade-guard` に残す。通すときは必ず `-z`（自己更新なし）を付ける。
   - `--kernel=FILE --rootfs=FILE`（片方だけでもよい）：このSoC用のuImageで1856K以下、squashfsで4992K以下、どちらも途中で切れていないもの
   - `--archive=FILE`：通常版パッケージ（`uImage.*`・`rootfs.squashfs.*` とそのmd5sum）で、両方が区画に入るもの。`firmware.bin` やほかのファイルを含むものは断る
   - `-k -r`：`--archive` と一緒のときだけ（Web UIのアップロードがこの形で渡す）
   - `-n -x -s -z --web --insecure`、読むだけの `-h --help --history --list-builds`
   - `/rom` の更新処理が、中身を読んで区画を守ると確かめた版（v1.0.84）でなければ、書き込みはすべて断る
2. **envの `upgrade`** に、名前解決できないURL（`https://iblack-fixed-slots.invalid/no-online-update.tgz`）を入れる。更新処理は `--url` などの指定がないとこのURLから取ろうとして失敗するので、ガードが消えた状態でもWeb UIの更新ボタン・`sysupgrade -k -r`・自動更新は何も取得できない。`--channel`・`--build` は防げないので補助の層。

どちらも `easy/post_setup.py` が入れる（[../easy/README.md](../easy/README.md)）。新しく移す機体には `make_env.py` が最初からenvに入れる。

```sh
sysupgrade --guard-status          # カメラで。ガードの版・区画・更新処理の版・断るもの・envのupgradeを表示
rm /overlay/usr/sbin/sysupgrade    # ガードを外す（/romの更新処理に戻る）
```

工場出荷リセット（`sysupgrade -n`、`firstboot`、Web UIのリセット）はoverlayごとガードを消す（envは残る）。所有者がSSHで初回ログイン（EULAとrootパスワード）をしてから、`post_setup.py` を流し直す。

## 区画に収まる版の入れ方（未実施）

```sh
cd research/openipc-migrate
python3 update/check_update.py openipc.hi3518ev100-nor-lite.tgz   # 0: 入る、1: 入らない（理由を表示）、2: 読めない
tar -xzf openipc.hi3518ev100-nor-lite.tgz uImage.hi3518ev100 rootfs.squashfs.hi3518ev100
C='ssh root@192.0.2.20'
$C 'cat > /tmp/uImage.hi3518ev100' < uImage.hi3518ev100
$C 'cat > /tmp/rootfs.squashfs.hi3518ev100' < rootfs.squashfs.hi3518ev100
K=--kernel=/tmp/uImage.hi3518ev100 Rf=--rootfs=/tmp/rootfs.squashfs.hi3518ev100
$C SYSUPGRADE_GUARD_DRYRUN=1 sysupgrade $K $Rf   # 判定だけ。"would run:" なら通る
$C sysupgrade $K $Rf                             # 書き込んで再起動（overlayの設定は残る）
$C sysupgrade --guard-status                     # 再起動の後。ガードが残り、更新処理の版が known か
```

`check_update.py --kernel FILE --rootfs FILE` で取り出した後のイメージも調べられ、`--json` で結果をJSONで出す。2つのイメージで約7 MBが `/tmp`（14 MB）に入る。`/tmp` はRAM（全体28 MB）を使う。更新処理（v1.0.84）は、メモリが足りなければmajesticを止めてから進み、それでも足りなければ何も書かずにmajesticを戻す（コードを読んで確かめただけで、実機では未確認）。

`--archive`（Web UIのアップロードを含む）もガードは同じ基準で判定するが、`/tmp` に約6.9 MBのアーカイブと展開後の約7 MBが同時に要り、足りない恐れがある。実機では試していないので、当面は上の `--kernel`/`--rootfs` を使う。

rootfsを入れ替えると `/rom` の更新処理の版が変わることがある。読んでいない版ではガードが書き込みをすべて断るので、その版の `--kernel`/`--rootfs` の処理が区画を守ることを読んで確かめてから `sysupgrade-guard.sh` の `KNOWN_UPDATERS` に加え、`post_setup.py` で入れ直す。

## 守ること

- Web UIのGitHubからの更新と自動更新は使わない・有効にしない。
- `sysupgrade -u`（U-Boot）は使わない。ガードも断る。
- 入れるのは、`check_update.py` が入ると判定した通常版パッケージだけ。分割版（`-split.tgz`、`firmware.bin`）は使わない。

## 確認

結果は [../results/unit2-update-guard.json](../results/unit2-update-guard.json)。

| 対象 | 内容 |
| --- | --- |
| 2台目のカメラ | ガードを `/tmp` に置き、`SYSUPGRADE_GUARD_DRYRUN=1`（判定だけ。更新処理は起動しない）で28件、すべて期待どおり。アーカイブ13件：収まるものを `-k -r --web` 付き・なしで通し、rootfs超過・kernel超過・`firmware.bin` 入り・別SoC・余分なファイル入り・`--force_ver`（2件）・アーカイブなしの `-k -r`（2件）・`--build=latest`・存在しないファイルを断る。実機のkernel・rootfsのヘッダーから作った疎ファイルで15件：`--kernel --rootfs`・`-x` 付き・rootfsだけ・`-n` を通し、区画を超えるもの（kernelは1バイト、rootfsは4 KiB）・途中で切れたもの（kernel・rootfs）・入れ替え・`--archive` との併用・`--no_ramfs`・`-f`・`-u`・`--url`・`--channel` を断る |
| 配布パッケージ | `check_update.py` で、いまの版の通常版は入る（終了コード0）、nightly（`4cc6476`）はrootfsが12,288 B超過で入らない（1） |
| `tests/test_sysupgrade_guard.py`（20件） | ホストのBusyBox ashで、置き換えた更新処理を相手に判定と渡す引数を確かめる |
| `tests/test_check_update.py`（9件） | `tests/update_fixture.py` が作る試験用のuImage・squashfs・パッケージで判定を確かめる |

```sh
python3 -m unittest tests.test_sysupgrade_guard tests.test_check_update
```
