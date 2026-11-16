# 2台目の映像（Hi3518EV100＋AR0141、OpenIPC）

結果は [../results/unit2-video.json](../results/unit2-video.json)。カメラ上の変更は、overlay（jffs2のrootfs_data）の3ファイルとU-Boot envの `sensor` だけ。U-Bootもfirmwareも書いていない。

## 状態（2026-11-16）

- 1280×720のH.264（Main、Level 3.1）を約27 fps・約1 Mbpsで出す。RTSPは `rtsp://<カメラ>:554/stream=0`、JPEGは `http://<カメラ>/image.jpg`、Web画面は `http://<カメラ>/`。どれもrootのパスワードが要る。
- 起動のたびに、最初のmajesticを1回だけ自動で起動し直す（下の「起動直後に映像が出ない」）。再起動から約50秒で映像が出る。
- 動作中に映像が止まっても、`S96vicheck` が30〜40秒で気づいてmajesticを起動し直す。

## カメラ上の変更

| 場所 | 内容 | リポジトリ |
|---|---|---|
| U-Boot envの `sensor` | `ar0141`。初回起動の自動検出は、`insert_sns` の一覧にないIDを保存していた | — |
| `/etc/sensors/ar0141_i2c_720p.ini` | AR0141の設定。`libsns_ar0140.so`、DC 12ビット並列、1280×720、`Isp_Bayer=1`（GRBG） | [ar0141_i2c_720p.ini](ar0141_i2c_720p.ini) |
| `/etc/majestic.yaml` | `isp.blkCnt: 9` だけ。ほかはmajesticの既定値（元のfirmwareにこのファイルはない） | [majestic.yaml](majestic.yaml) |
| `/etc/init.d/S96vicheck`（755） | 起動直後の確認と、動作中の停止の監視 | [S96vicheck](S96vicheck) |

`sensor=ar0141` にすると、`load_hisilicon` はセンサークロックを24 MHzにし（`0x20030030=0x1`）、センサーから先のモジュールを読み込む（それまでは `report_error` で抜けていた）。majesticはセンサー名から `/etc/sensors/ar0141_i2c_720p.ini` を選び、IQは `default.ini` を使う。

## 分かったこと

### Bayer配列はGRBG

センサーのテストパターン（`0x3070=1` で単色。`0x3072` が赤、`0x3074` がGr、`0x3076` が青、`0x3078` がGb）で赤だけを出すと、JPEGの平均はR 0.82、G 0.41、B 0.56（標準偏差0.003）だった。赤が赤として出るので、`Isp_Bayer=1`（GRBG）で合っている。

### VBの数

ISPのVB数（`isp.blkCnt`）がmajesticの自動値（6）だとVbFailが増え、VIは17〜18 fpsしか出なかった。9にするとVbFailは0、VIは26〜27 fps、VENCは27 fpsになった。MMZの空きは約11 MB残る。

### 起動直後に映像が出ない

起動して最初のmajesticでは映像が出ない（VIの `IntCnt` が0のまま。ログは `Timeout from venc channel 0`）。2回目からの起動では必ず出る。

- VIUのポートとチャネルの有効ビット（`0x20580100`、`0x20581000`）は、センサーがPIXCLKを出している間しか書込みが残らない。
- majesticはVIを有効にしてからセンサーを初期化する。起動直後のAR0141はソフトスタンバイでPIXCLKを出していないので、有効ビットが残らない。
- majesticが終わってもセンサーは出力を続ける（`0x301A` は `0x10DC` のまま）ので、次の起動では有効ビットが残る。
- 動作中に `0x301A` のstreamビットを落としてからmajesticを起動すると、同じ症状が出た。

`S96vicheck` は `S95majestic` の後に動く。VIが設定済みなのに8秒たってもフレームが来なければ、majesticを1回だけ起動し直す。

### 動作中の停止と監視

動作中にテストパターンを何度も切り替えたところ、VIUのレジスタがすべて0になって映像が止まった（majesticは動いたまま、JPEGは503）。majesticを起動し直すと戻った。通常の使い方で起きるかは分からない。

そこで `S96vicheck` は起動後も10秒ごとに `/proc/umap/vi` の `IntCnt` を見る。同じmajesticのプロセスで値が30秒変わらなければ、majesticを起動し直す。センサーが止まっていたときは、1回目の起動し直しでもフレームが来ない（上と同じ理由）。そのため続けて3回まで試し、その後は10分おきにする。majesticのPIDが変われば（利用者、Web画面、supervisorによる起動し直し）数え直すので、意図した起動し直しを停止とはみなさない。

## 実機での確認（2026-11-16）

- 再起動: 起動直後の確認がmajesticを1回起動し直し（`VI configured but no frame from the sensor (cold start); restarting majestic once` → `video input running after one restart`）、uptime 49秒で映像が出ていた。
- 停止の試験: 動作中に `0x301A` のstreamビットを落としてセンサーを止めた。約35秒後に1回目、約82秒後に2回目の起動し直しがあり、映像が戻った（`video input running again after 2 restart(s)`）。
- 再起動後の測定: VI 27 fps、VENC 27 fps、JPEG 1280×720。RTSPはH.264 Main L3.1 1280×720、26.7 fps、約1.0 Mbps、IDRは約1秒ごと。空きRAMはキャッシュ込みで約12 MB。
- オフライン試験: [`tests/test_vicheck.py`](../tests/test_vicheck.py)。ホストのBusyBox ashで8つの場面を流す。

## 入れ方（SSHでrootになれる別の機体）

```sh
ssh root@<カメラ> fw_setenv sensor ar0141
ssh root@<カメラ> 'cat > /etc/sensors/ar0141_i2c_720p.ini' < ar0141_i2c_720p.ini
ssh root@<カメラ> 'cat > /etc/majestic.yaml' < majestic.yaml   # 既存の設定があるなら isp.blkCnt だけ足す
ssh root@<カメラ> 'cat > /etc/init.d/S96vicheck && chmod 755 /etc/init.d/S96vicheck' < S96vicheck
ssh root@<カメラ> reboot
```

## 元に戻す

`rm /etc/init.d/S96vicheck /etc/majestic.yaml /etc/sensors/ar0141_i2c_720p.ini` のあと、`fw_setenv sensor`（値なし）で消して再起動する。消すと `load_hisilicon` は起動時に `ipcinfo --short-sensor` で検出し直すので、この機体では映像の出ない状態に戻る。

## 残っていること

- IQはOpenIPCの `default.ini` のまま（AR0141向けの調整はしていない）。露出とホワイトバランスは自動で動く。
- 暗視（IRカットフィルターと赤外LED）のGPIOは調べていない。
- PHYのないeth0が起動され、カーネルログに `tx timeout` が繰り返し出る。映像とWi-Fiには影響がないので、そのままにしている。
- ホスト名に、初回起動で検出した誤ったセンサーIDが残っている（表示だけ）。
- 動作中にI2Cでセンサーのレジスタを書くと（`ipctool i2cset` など）、映像が止まることがある。監視が戻すが、試験では気をつける。

[rtspprobe.py](rtspprobe.py) は測定に使ったRTSPの確認ツール。集計値だけを表示し、アドレスとパスワードはリポジトリ外のファイルから読む。
