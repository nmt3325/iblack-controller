# 2台目の音声（OpenIPCのmajestic）: マイクとスピーカー

結果は [../results/unit2-audio.json](../results/unit2-audio.json)（マイク）と [../results/unit2-speaker.json](../results/unit2-speaker.json)（スピーカー）。カメラ上の変更は、overlay（jffs2のrootfs_data）の2ファイルと、`/etc/majestic.yaml` の `audio` の設定だけ。U-Bootもfirmwareも書いていない。

## 状態（2026-11-17）

- マイクの音が、RTSP（`?audio=opus`）、`/video.mp4`、`/audio.opus`、`/audio.m4a` で正しい長さと高さで出る。直す前はどれも音の半分しか入っておらず、1オクターブ高く、欠けた所を補う音で雑音だらけに聞こえた。WebRTCも同じOpusの経路を通る（WebRTCでは直接測っていない）。
- `/audio.pcm` と `/audio.alaw` は直す前から正しい。今回の変更でも変わらない。
- マイクのゲインは変えていない（下の「マイクのゲイン」）。
- スピーカー: `/play_audio` に送った音は、正しい高さと長さで鳴る（カメラのマイクで録り直して確かめた）。ただし既定の設定では鳴らない。`audio.outputEnabled` が既定でfalseで、アンプのピン `audio.speakerPin` も入っていない。[`easy/post_setup.py`](../easy/README.md) がピン（63）を入れる。鳴らすかどうか（`outputEnabled`）は使う人が決める。
- スピーカーの音量 `audio.outputVolume` は、majesticでは何も変えなかった。ライブラリで純正と同じ効き方にした（50で等倍、0で無音、変えるとすぐ効く）。
- マイクも既定では切ってある。`audio.enabled` が既定でfalseで、`/audio.opus` は501（`Server audio is not enabled`）を返す（2台目では所有者が入れてあった）。`audio.enabled` と `audio.outputEnabled` は `cli -s` で変えるとすぐ効き、majesticの再起動は要らない（下の「マイクとスピーカーを使う」）。

## カメラ上の変更

| 場所 | 内容 | リポジトリ |
|---|---|---|
| `/usr/lib/libmajaudiofix.so`（644、4,736バイト） | majesticが呼ぶ `HI_MPI_AI_SetPubAttr`（マイク）と、`HI_MPI_ADEC_CreateChn`・`HI_MPI_ADEC_SendStream`（スピーカーの音量）を包むライブラリ | [libmajaudiofix.so](libmajaudiofix.so)（ソースは [majaudiofix.S](majaudiofix.S)） |
| `/etc/init.d/S95majestic`（755） | 上のライブラリを `LD_PRELOAD` に入れて、firmwareの `/rom/etc/init.d/S95majestic` に渡す | [S95majestic](S95majestic) |
| `/etc/majestic.yaml` の `audio` | `speakerPin: 63`（アンプのGPIO）。2台目では鳴らすので `outputEnabled: true` も入れた | [../night/majestic.yaml](../night/majestic.yaml)（`speakerPin` だけ） |

マイクだけの最初の版は [`easy/post_setup.py`](../easy/README.md) で入れた。音量を足した版は、手で入れ替えて確かめた後、修正を外した状態とマイクだけの版の状態の両方から `post_setup.py`（版3）で入れ直した（下の「入れ方」）。

## マイク: 原因

majestic（`Lite HiSilicon (hi3516cv100), master+fa91671, 2026-11-13`）の音声入力の初期化（`0x78dd8`）は、AIの設定 `AIO_ATTR_S` の `u32ChnCnt` を常に2にして `HI_MPI_AI_SetPubAttr` に渡す（`0x78e28` の `mov r4, #2`。呼び出しは `0x78af0` の1か所）。内蔵コーデックのI2Sはモノラルでも2チャネルで動く（SDKの `hi_comm_aio.h` では2/4/8/16）ので、ドライバーにはこれで正しい。

ところがmajesticは、同じ `u32ChnCnt` を自分の音声パイプライン（エンコーダーの入力）のチャネル数にも使う（`0x790a0` の `ldrb r2, [sp, #56]` → `0x75730`）。パイプラインに流すのはAIのチャネル0（モノラル）だけなので、エンコーダーはモノラルのサンプル列を左右交互のステレオとして受け取る。

- Opus: 20 ms分のモノラル160サンプルが10 msのステレオとして符号化され、20 ms分の時刻が付く（TOCは構成0＝SILK NB 10 ms、ステレオ）。再生すると20 msごとに10 ms分しか音がなく、高さは1オクターブ上がる（部屋の90 Hzの音が180 Hzに出ていた）。残りは欠落として補われるので、雑音に聞こえる。
- AAC（`/audio.m4a`）: ステレオとして出て、録った時間の半分の長さしかない。
- PCMとA-law: 正しい長さと高さで出ていた。マイクとアナログ部は原因ではない（既定のゲインでPCMにクリップも段差もない）。

## マイク: 純正firmwareとの比較

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

## マイク: 直し方

`libmajaudiofix.so` は、majesticが呼ぶMPIの関数を包む小さな共有ライブラリ（ARMのアセンブリ、[majaudiofix.S](majaudiofix.S)）。マイクには `HI_MPI_AI_SetPubAttr` を包む（スピーカーの分は下の「スピーカー: 直し方」）。

1. 本物の `HI_MPI_AI_SetPubAttr`（`libmpi.so`）を `dlsym(RTLD_NEXT)` で探し、受け取った設定のまま呼ぶ。ドライバーには今までどおり `u32ChnCnt` 2 が渡る（`/proc/umap/ai` でも `ChnCnt 2`、チャネル0と1が有効のまま）。
2. 成功し（戻り値0）、モノラル（`enSoundmode` 0）で `u32ChnCnt` が2のときだけ、呼び出し側の構造体の `u32ChnCnt` を1にする。
3. majesticはその値をパイプラインのチャネル数に使うので、エンコーダーはモノラルとして扱う。

失敗したときとステレオの設定では何もしない。majesticは音声を始めるたびにこの構造体を作り直して呼ぶので、設定の再読込み（SIGHUP）や起動し直しの後も効く。

`S95majestic` は、ライブラリを `LD_PRELOAD` の先頭に入れ（すでにあれば入れない）、`exec sh /rom/etc/init.d/S95majestic "$@"` でfirmwareのスクリプトに渡すだけ。起動・停止・再起動・reloadとsupervisor（`$0` の名前で探す）はfirmwareのままになる。ライブラリがなければ素通しする。

## マイク: 実機での確認（2026-11-17）

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
- オフライン試験: [`tests/test_majaudiofix.py`](../tests/test_majaudiofix.py)（26件。ライブラリのELFの形と命令列、ARMのエミュレーターで動かす3つの関数、ホストのBusyBoxで動かす `S95majestic`、`opusstat.py`）と [`tests/test_post_setup.py`](../tests/test_post_setup.py)（16件）。

## マイクのゲイン（任意）

純正のマイクのゲインは27・ブーストなし、majesticの既定は20・ブーストあり。`audio.volume` を30にすると、acodecは純正と同じ値（27、ブーストなし、`0x20050068` = `0x00F26C6C`）になった。静かな部屋のPCMは、RMS 22.2（−63.4 dBFS）から5.6（−75.4 dBFS）へ約12 dB下がる（ゲインなので声も小さくなる）。試しただけで元に戻し、今回の修正には入れていない。純正と同じにしたいときは:

```sh
ssh root@<カメラ> 'cli -s .audio.volume 30 && /etc/init.d/S95majestic restart'
```

## スピーカー: 経路

majesticの再生の経路を逆アセンブルで追い、実機で確かめた。

1. 入口は3つ。`POST /play_audio`（生のPCM `audio/L16; rate=…; channels=…`（s16le、4,000〜192,000 Hz、1か2チャネル）か、Ogg Opus）、双方向通話（WebRTC）のOpus、プラグイン用のunixソケット `audio.player`（1回2,048バイトまで）。
2. 音は262,144バイトのリングに入り、640バイトずつ8 kHzモノラルにリサンプルされる。
3. どの入口も `HI_MPI_ADEC_SendStream`（LPCM、ブロッキング）の1か所を通り、ADEC → AO（デバイス0、チャネル0、8 kHz）で鳴る。純正の `cmsMain.out` はAOを44.1 kHzで使う。
4. 鳴らし始めに `audio.speakerPin` のGPIOを1にしてアンプを入れ、音が `speakerPinHoldMs`（既定2,000 ms）来なければ0に戻す。

経路そのものに誤りはなかった。

## スピーカー: 見つけた問題

1. 既定の設定では鳴らない。`audio.outputEnabled` が既定でfalse、`audio.speakerPin` は未設定で、アンプが入らない。このボードのアンプはGPIO63で、1で入る（`speakerPinInvert` は既定のfalseのまま）。`post_setup.py`（版3）が、ほかのボードの設定と同じように `audio.speakerPin: 63` を入れる。`outputEnabled` は入れない（鳴らすかどうかは使う人が決める）。
2. 音量 `audio.outputVolume` が効かない。majesticはこのチップ（hi3516cv100系）ではスピーカーの音量を何もしない（値を変えると音声を作り直すだけ）。このSDKの `HI_MPI_AO_SetVolume` は `0xA0168008`（NOT_SUPPORT）を返す。純正の `cmsMain.out` は、サンプルに自分で倍率を掛けている（音量vで (v−50)×0.6 dB）。

## スピーカー: 直し方

`libmajaudiofix.so` が、純正と同じ倍率をサンプルに掛ける。

- `HI_MPI_ADEC_CreateChn`: majesticはスピーカーを始めるたび（設定を変えたときも）ADECのチャネルを1つ（LPCM）作る。本物の呼び出しが成功したら、majesticの `config_get_int` で `audio.outputVolume` を読んで倍率を決める。0は無音、1〜100は (v−50)×0.6 dB（−29.4〜+30 dB）、未設定は50（等倍）。範囲の外は0〜100に丸める。syslogに `majaudiofix: speaker outputVolume 50 -> gain 65536/65536` のように出す。LPCM以外のときと、majesticの外（`config_get_int` がない）では等倍のまま。
- `HI_MPI_ADEC_SendStream`: 等倍ならそのまま渡す。そうでなければ、サンプルをスタックに写して倍率を掛け（四捨五入して16ビットに飽和）、写した方を渡す。呼び出し側のバッファは変えない。空のもの、NULL、8,192バイトを超えるもの（majesticは640バイト、多くて2,048バイト）はそのまま渡す。

そのため `cli -s .audio.outputVolume 70` などは、majesticを起動し直さなくてもすぐ効く。

## スピーカー: 実機での確認（2026-11-17）

日中に、`/play_audio` に8 kHzの小さめの音（1〜4.5秒）を送り、カメラのマイク（`/audio.pcm`）で録り直した。

| 送った音（`outputVolume` 50） | マイクで録った音 |
|---|---|
| 1 kHz、−20 dBFS、1秒 | 1000.0 Hz、−29.1 dBFS。2倍音−54 dB、3倍音−58 dB |
| 500、1,000、2,000 Hz、−26 dBFS、各1.5秒 | 500.5、1000.0、2000.0 Hzの順、合わせて約4.4秒 |

- GPIO63（アンプ）は鳴っている間だけ1で、音が終わって約2秒後に0に戻る。
- `/proc/umap/adec` の `SendCnt` は1秒の音で25（640バイトずつ）、`SendFail` は0。
- 500 Hzでは筐体がびびる（基本波よりほかの成分の方が5 dB強い）。2 kHzは1 kHzより約13 dB小さい。スピーカーと筐体の性質で、処理の誤りではない。

音量（1 kHz。70と100は、大きな音を出さないよう、送る音を同じだけ小さくした）:

| `outputVolume` | 送った音 | 録った1 kHz | 50との差 | 期待 |
|---|---|---|---|---|
| 50（既定） | −20 dBFS | −28.8 dBFS | 0 dB | 0 dB |
| 30 | −20 dBFS | −40.8 dBFS | −12.0 dB | −12 dB |
| 0 | −20 dBFS | 雑音だけ（900〜1,100 Hzで−85.8 dB） | 無音 | 無音 |
| 70 | −32 dBFS | −28.8 dBFS | +12.0 dB | +12 dB |
| 100 | −50 dBFS | −28.8 dBFS | +30.0 dB | +30 dB |

- 値を変えるたびにsyslogに倍率が出て、同じmajesticのプロセスのまますぐ効いた。最後に `cli -d .audio.outputVolume` で既定（50、等倍）に戻した。
- 入れ替えて再起動した後も、majesticはライブラリを読み込み（起動直後の `S96vicheck` による起動し直しの後も）、1 kHzは−29.0 dBFSで鳴り、マイクは20 msのモノラルのまま（4秒で198パケット）。`post_setup.py --check` は「変更なし」。
- オフライン試験: [`tests/test_majaudiofix.py`](../tests/test_majaudiofix.py) は、ライブラリの3つの関数をARMv5の小さなエミュレーター（[`tests/arm_emu.py`](../tests/arm_emu.py)）で動かし、音量と倍率の対応、サンプルの計算、そのまま渡す場合、エラーを確かめる。

## マイクとスピーカーを使う

majesticの既定では、マイクもスピーカーも切ってある。`post_setup.py` は直すだけで、これは変えない。

```sh
ssh root@<カメラ> 'cli -s .audio.enabled true'         # マイク。falseの間、/audio.opus は501（Server audio is not enabled）
ssh root@<カメラ> 'cli -s .audio.outputEnabled true'   # スピーカー。falseの間、/play_audio は501（Audio output is not enabled）
ssh root@<カメラ> 'cli -s .audio.outputVolume 40'      # 任意。50で等倍、0で無音、100で+30 dB
curl -u root:<パスワード> -H 'Content-Type: audio/L16; rate=8000; channels=1' --data-binary @a.raw http://<カメラ>/play_audio
```

どれもmajesticを再起動せずにすぐ効く（2台目で確かめた）。スピーカーにはアンプのピン `audio.speakerPin: 63` も要る。`post_setup.py` が入れる（手でなら `cli -s .audio.speakerPin 63`）。`a.raw` は8 kHz・16ビット・モノラルの生のPCM。大きい音量では、大きな音は16ビットで頭打ちになる。

## 入れ方

[`easy/post_setup.py`](../easy/README.md) が入れる（2台目でもこれを使った）。

```sh
python3 easy/post_setup.py --ssh 'ssh root@192.0.2.20' --check    # 変わる所を表示するだけ
python3 easy/post_setup.py --ssh 'ssh root@192.0.2.20' --reboot   # 入れて再起動
```

`/etc/init.d/S95majestic` がfirmwareのもの（`/rom` と同じ）か、このディレクトリの以前の版のときだけ入れる。ほかの内容に置き換わっていれば、何も変えずにそう表示する。

2台目では、修正を外して `audio.speakerPin` も消した状態からと、マイクだけの版を入れた状態からの両方で、`--check` が3つ（`audio.speakerPin`、ライブラリ、`S95majestic`）を挙げ、`--reboot` の後は「変更なし」になった。どちらも、再起動後のmajesticはライブラリを読み込み、マイクは20 msのモノラル、1 kHzは−29.1〜−29.2 dBFS、`outputVolume 30` で−41.1〜−41.2 dBFS（−12.0 dB）だった。`easy_migrate.py` の最後の段階（`post_setup`）から入れても同じだった（[../results/unit2-speaker.json](../results/unit2-speaker.json) の `post_setup_install`）。

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
ssh root@<カメラ> 'logread | grep majaudiofix | tail -1'   # outputEnabledがtrueなら、例: speaker outputVolume 50 -> gain 65536/65536
```

[opusstat.py](opusstat.py) はOggのOpusHeadと各パケットのTOCを数えるだけ（復号はしない）。RTSPの音は `ffmpeg -rtsp_transport tcp -i 'rtsp://<カメラ>:554/stream=0?audio=opus' -map 0:a -c copy -t 6 -f ogg x.ogg` でOggにしてから渡す。この場合のOpusHeadはSDPに合わせて常に2チャネルなので、`toc_stereo` と `frame_ms` を見る。

## 元に戻す

```sh
ssh root@<カメラ> 'rm /overlay/etc/init.d/S95majestic /overlay/usr/lib/libmajaudiofix.so && reboot'
```

`/overlay` を付けずに `/etc/init.d/S95majestic` を消すと、このoverlayではfirmwareの `S95majestic` まで隠れて、majesticが起動しなくなる。必ず `/overlay` の下を消す。ライブラリを消すと、マイクの修正もスピーカーの音量も外れる。

スピーカーの設定は `ssh root@<カメラ> 'cli -d .audio.outputEnabled; cli -d .audio.speakerPin'` で既定（鳴らない）に戻る。

## 作り直し

Cコンパイラーは要らない。GNU binutils（as、ld、strip）と、カメラのOpenIPCのrootfs（muslの `lib/libc.so`）を使う。

```sh
dpkg-deb -x binutils-arm-linux-gnueabi_2.40-2_amd64.deb /tmp/armbin    # Debian 12のパッケージ
audio/build.sh /tmp/armbin <展開したOpenIPCのrootfs> /tmp/libmajaudiofix.so
sha256sum /tmp/libmajaudiofix.so   # 2a7bc0d4aa9f3783a235ee2a7496e9cb74ddafe57eded70ad566fc4c5e56d120
```

`OM_ARM_BINUTILS` と `OM_OIPC_ROOTFS` にこの2つを入れて `tests/test_majaudiofix.py` を流すと、作り直したものがリポジトリのファイルとバイト単位で一致することも確かめる。

## 残っていること

- WebRTCの音は直接測っていない（同じOpusの経路）。
- スピーカーは `/play_audio` の生のPCMだけで鳴らした。双方向通話（WebRTC）、Ogg Opus、プラグインの `audio.player` からの音は試していない（どれも同じ `HI_MPI_ADEC_SendStream` を通るので、音量も同じく効くはず）。
- シェルから `majestic` を直接起動すると修正は効かない。`LD_PRELOAD=/usr/lib/libmajaudiofix.so majestic` のように起動する。
- 初期化（`firstboot`、Web画面のリセット）でoverlayが消えると、修正も消える。`post_setup.py` をもう一度流す。
- majesticやfirmwareを更新したら、修正を外して（上の「元に戻す」）`opusstat.py` で確かめる。上流で直っていればそのまま、直っていなければ `post_setup.py` で入れ直す。
