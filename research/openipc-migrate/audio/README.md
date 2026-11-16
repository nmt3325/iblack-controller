# 2台目のマイク（OpenIPCのmajestic）

結果は [../results/unit2-audio.json](../results/unit2-audio.json)。カメラ上の変更は、overlay（jffs2のrootfs_data）の2ファイルだけ。U-Bootもfirmwareも書いていない。`/etc/majestic.yaml` も変えていない。

## 状態（2026-11-17）

- マイクの音が、RTSP（`?audio=opus`）、`/video.mp4`、`/audio.opus`、`/audio.m4a` で正しい長さと高さで出る。直す前はどれも音の半分しか入っておらず、1オクターブ高く、欠けた所を補う音で雑音だらけに聞こえた。WebRTCも同じOpusの経路を通る（WebRTCでは直接測っていない）。
- `/audio.pcm` と `/audio.alaw` は直す前から正しい。今回の変更でも変わらない。
- マイクのゲインは変えていない（下の「ゲイン」）。

## カメラ上の変更

| 場所 | 内容 | リポジトリ |
|---|---|---|
| `/usr/lib/libmajaudiofix.so`（644、4,688バイト） | majesticが呼ぶ `HI_MPI_AI_SetPubAttr` を包むライブラリ | [libmajaudiofix.so](libmajaudiofix.so)（ソースは [majaudiofix.S](majaudiofix.S)） |
| `/etc/init.d/S95majestic`（755） | 上のライブラリを `LD_PRELOAD` に入れて、firmwareの `/rom/etc/init.d/S95majestic` に渡す | [S95majestic](S95majestic) |

どちらも [`easy/post_setup.py`](../easy/README.md) で入れた。

## 原因

majestic（`Lite HiSilicon (hi3516cv100), master+fa91671, 2026-11-13`）の音声入力の初期化（`0x78dd8`）は、AIの設定 `AIO_ATTR_S` の `u32ChnCnt` を常に2にして `HI_MPI_AI_SetPubAttr` に渡す（`0x78e28` の `mov r4, #2`。呼び出しは `0x78af0` の1か所）。内蔵コーデックのI2Sはモノラルでも2チャネルで動く（SDKの `hi_comm_aio.h` では2/4/8/16）ので、ドライバーにはこれで正しい。

ところがmajesticは、同じ `u32ChnCnt` を自分の音声パイプライン（エンコーダーの入力）のチャネル数にも使う（`0x790a0` の `ldrb r2, [sp, #56]` → `0x75730`）。パイプラインに流すのはAIのチャネル0（モノラル）だけなので、エンコーダーはモノラルのサンプル列を左右交互のステレオとして受け取る。

- Opus: 20 ms分のモノラル160サンプルが10 msのステレオとして符号化され、20 ms分の時刻が付く（TOCは構成0＝SILK NB 10 ms、ステレオ）。再生すると20 msごとに10 ms分しか音がなく、高さは1オクターブ上がる（部屋の90 Hzの音が180 Hzに出ていた）。残りは欠落として補われるので、雑音に聞こえる。
- AAC（`/audio.m4a`）: ステレオとして出て、録った時間の半分の長さしかない。
- PCMとA-law: 正しい長さと高さで出ていた。マイクとアナログ部は原因ではない（既定のゲインでPCMにクリップも段差もない）。

## 純正firmwareとの比較

純正appfsのダンプの `cmsMain.out` を逆アセンブルして比べた（純正firmwareは動かしていない）。

| 項目 | 純正 | majestic（既定） |
|---|---|---|
| AIのサンプリング周波数 | 44.1 kHz（1/4の11,025 Hzにリサンプル） | 8 kHz |
| ビット幅・モード | 16ビット、I2Sマスター、モノラル | 同じ |
| 1フレームのサンプル数 | 884（約20 ms） | 160（20 ms） |
| `u32ChnCnt` | 2 | 2 |
| `u32ClkSel` | 1 | 0 |
| `u32EXFlag`、`u32FrmNum` | 1、30 | 0、30 |
| 使うチャネル | AIのチャネル0（モノラル） | 同じ |
| マイクのゲイン（acodec） | 27、ブーストなし | 20、ブーストあり（`0x20050068` = `0x00F25252`） |

純正も `u32ChnCnt` は2で、チャネル0をモノラルとして使う。ドライバーに2を渡すのは正しく、誤りはmajesticがその値をエンコーダーのチャネル数に使い回すところだけ。

## 直し方

`libmajaudiofix.so` は、`HI_MPI_AI_SetPubAttr` だけを持つ共有ライブラリ（ARMのアセンブリで28命令、[majaudiofix.S](majaudiofix.S)）。

1. 本物の `HI_MPI_AI_SetPubAttr`（`libmpi.so`）を `dlsym(RTLD_NEXT)` で探し、受け取った設定のまま呼ぶ。ドライバーには今までどおり `u32ChnCnt` 2 が渡る（`/proc/umap/ai` でも `ChnCnt 2`、チャネル0と1が有効のまま）。
2. 成功し（戻り値0）、モノラル（`enSoundmode` 0）で `u32ChnCnt` が2のときだけ、呼び出し側の構造体の `u32ChnCnt` を1にする。
3. majesticはその値をパイプラインのチャネル数に使うので、エンコーダーはモノラルとして扱う。

失敗したときとステレオの設定では何もしない。majesticは音声を始めるたびにこの構造体を作り直して呼ぶので、設定の再読込み（SIGHUP）や起動し直しの後も効く。

`S95majestic` は、ライブラリを `LD_PRELOAD` の先頭に入れ（すでにあれば入れない）、`exec sh /rom/etc/init.d/S95majestic "$@"` でfirmwareのスクリプトに渡すだけ。起動・停止・再起動・reloadとsupervisor（`$0` の名前で探す）はfirmwareのままになる。ライブラリがなければ素通しする。

## 実機での確認（2026-11-17）

同じ静かな部屋で、カメラから取り出した音を比べた。

| 経路（録った時間） | 直す前 | 直した後（入れて再起動） |
|---|---|---|
| `/audio.opus`（7秒） | 2チャネル、341パケット、10 msのステレオ → 3.41秒分（0.49倍） | 1チャネル、341パケット、20 msのモノラル → 6.82秒分（0.97倍） |
| RTSP `?audio=opus`（6秒） | 301パケット、10 msのステレオ → 3.01秒分（0.50倍） | 300パケット、20 msのモノラル → 6.00秒分（1.00倍） |
| `/video.mp4`（7秒）の音 | 3.40秒分 | 6.76秒分（映像は1280×720、180フレーム） |
| `/audio.m4a`（6秒） | AACステレオ、2.944秒分 | AAC 8 kHzモノラル、5.888秒分 |
| Opusを復号した音の強い成分 | 180、86、199 Hz | 43、90、16、133 Hz（PCMと同じ） |
| `/audio.pcm`（6秒） | RMS 22.8（−63.1 dBFS）、クリップ0 | RMS 22.2（−63.4 dBFS）、クリップ0 |

ほかに確かめたこと:

- `post_setup.py --check` で変わるのがこの2ファイルだけなのを見てから、`--reboot` で入れた。再起動後の `--check` は「変更なし」。
- 再起動後のmajesticはライブラリを読み込み、環境に `LD_PRELOAD=/usr/lib/libmajaudiofix.so` がある。firmwareのsupervisor（`sh /rom/etc/init.d/S95majestic supervise`）も動いている。
- 起動直後の確認（[`S96vicheck`](../video/README.md)）がいつもどおりmajesticを1回起動し直したが、起動し直した後もライブラリは入っていた。
- `killall -HUP majestic`（同じプロセス）と `/etc/init.d/S95majestic restart`（新しいプロセス）の後も、20 msのモノラルのまま（7秒で6.82秒分、6秒で6.00秒分）。
- `/audio.alaw` は6秒で5.98秒分、`/image.jpg` は1280×720。ログに音声とライブラリのエラーはない。
- オフライン試験: [`tests/test_majaudiofix.py`](../tests/test_majaudiofix.py)（14件。ライブラリのELFの形と命令列、ホストのBusyBoxで動かす `S95majestic`、`opusstat.py`）と [`tests/test_post_setup.py`](../tests/test_post_setup.py)（16件）。

## ゲイン（任意）

純正のマイクのゲインは27・ブーストなし、majesticの既定は20・ブーストあり。`audio.volume` を30にすると、acodecは純正と同じ値（27、ブーストなし、`0x20050068` = `0x00F26C6C`）になった。静かな部屋のPCMは、RMS 22.2（−63.4 dBFS）から5.6（−75.4 dBFS）へ約12 dB下がる（ゲインなので声も小さくなる）。試しただけで元に戻し、今回の修正には入れていない。純正と同じにしたいときは:

```sh
ssh root@<カメラ> 'cli -s .audio.volume 30 && /etc/init.d/S95majestic restart'
```

## 入れ方

[`easy/post_setup.py`](../easy/README.md) が入れる（2台目でもこれを使った）。

```sh
python3 easy/post_setup.py --ssh 'ssh root@192.0.2.20' --check    # 変わる所を表示するだけ
python3 easy/post_setup.py --ssh 'ssh root@192.0.2.20' --reboot   # 入れて再起動
```

`/etc/init.d/S95majestic` がfirmwareのもの（`/rom` と同じ）か、このディレクトリの以前の版のときだけ入れる。ほかの内容に置き換わっていれば、何も変えずにそう表示する。

手で入れるときは、動いているmajesticが読み込んでいるファイルを上書きしないよう、別名で書いてから `mv` する。

```sh
ssh root@<カメラ> 'cat > /usr/lib/libmajaudiofix.so.new && chmod 644 /usr/lib/libmajaudiofix.so.new && mv /usr/lib/libmajaudiofix.so.new /usr/lib/libmajaudiofix.so' < audio/libmajaudiofix.so
ssh root@<カメラ> 'cat > /etc/init.d/S95majestic.new && chmod 755 /etc/init.d/S95majestic.new && mv /etc/init.d/S95majestic.new /etc/init.d/S95majestic' < audio/S95majestic
ssh root@<カメラ> reboot    # 再起動しないなら /etc/init.d/S95majestic restart
```

## 確かめ方

```sh
ssh root@<カメラ> 'grep -c libmajaudiofix /proc/$(pidof majestic)/maps'   # 1以上なら読み込まれている
curl -s -m 7 -u root:<パスワード> -o a.opus http://<カメラ>/audio.opus
python3 audio/opusstat.py a.opus 7   # 直っていれば channels 1、frame_ms [20]、decoded_per_wallclock が約1.0
```

[opusstat.py](opusstat.py) はOggのOpusHeadと各パケットのTOCを数えるだけ（復号はしない）。RTSPの音は `ffmpeg -rtsp_transport tcp -i 'rtsp://<カメラ>:554/stream=0?audio=opus' -map 0:a -c copy -t 6 -f ogg x.ogg` でOggにしてから渡す。この場合のOpusHeadはSDPに合わせて常に2チャネルなので、`toc_stereo` と `frame_ms` を見る。

## 元に戻す

```sh
ssh root@<カメラ> 'rm /overlay/etc/init.d/S95majestic /overlay/usr/lib/libmajaudiofix.so && reboot'
```

`/overlay` を付けずに `/etc/init.d/S95majestic` を消すと、このoverlayではfirmwareの `S95majestic` まで隠れて、majesticが起動しなくなる。必ず `/overlay` の下を消す。

## 作り直し

Cコンパイラーは要らない。GNU binutils（as、ld、strip）と、カメラのOpenIPCのrootfs（muslの `lib/libc.so`）を使う。

```sh
dpkg-deb -x binutils-arm-linux-gnueabi_2.40-2_amd64.deb /tmp/armbin    # Debian 12のパッケージ
audio/build.sh /tmp/armbin <展開したOpenIPCのrootfs> /tmp/libmajaudiofix.so
sha256sum /tmp/libmajaudiofix.so   # 2af6ee3b35958013f8d88f843f322968335e5a88b4aa69eea3fe1b1f25f88150
```

`OM_ARM_BINUTILS` と `OM_OIPC_ROOTFS` にこの2つを入れて `tests/test_majaudiofix.py` を流すと、作り直したものがリポジトリのファイルとバイト単位で一致することも確かめる。

## 残っていること

- WebRTCの音は直接測っていない（同じOpusの経路）。
- シェルから `majestic` を直接起動すると修正は効かない。`LD_PRELOAD=/usr/lib/libmajaudiofix.so majestic` のように起動する。
- 初期化（`firstboot`、Web画面のリセット）でoverlayが消えると、修正も消える。`post_setup.py` をもう一度流す。
- majesticやfirmwareを更新したら、修正を外して（上の「元に戻す」）`opusstat.py` で確かめる。上流で直っていればそのまま、直っていなければ `post_setup.py` で入れ直す。
