# 2台目の暗視（IRカットフィルターと赤外LED、OpenIPC）

結果は [../results/unit2-night.json](../results/unit2-night.json)。カメラ上の変更は、overlay（jffs2のrootfs_data）の2ファイルだけ（`/etc/majestic.yaml` に `nightMode` を足し、`/etc/init.d/S94lightsensor` を置いた）。U-Bootもfirmwareも書いていない。

## 状態（2026-11-16）

- 明るさセンサーの値で、majesticが昼と夜を自動で切り替える。夜はIRカットフィルターを外し、赤外LEDを点け、映像を白黒にする。昼はその逆。
- センサーの値が `minThreshold`（100）を下回ると夜、`maxThreshold`（150）を上回ると昼になる。その間では今の状態を保つ。判定は5秒ごと。
- Web画面とAPIから手動でも切り替えられ、しきい値なども変えられる（下の「切り替え方と設定」）。
- 2026-11-16の夕方は、16:24に自動で夜になった。

## ピンとセンサー

元のfirmware（`cmsMain`、BoardType `0x63002601`）を解析して見つけ、実機で確かめた。

| 部品 | つながる先 | 動き | 実機での確認 |
|---|---|---|---|
| IRカットフィルター | GPIO0_2（gpio 2） | 1本で駆動し、レベルを保つ。1で昼（フィルターが入る）、0で夜（外れる） | 外すとR/G比が1.108→1.187、露出が2337→905になった（赤外が入る） |
| 赤外LED | GPIO6_4（gpio 52） | 1で点灯。パッド（`0x200F0090`）の既定は1（SDIOのdata0）で、0にするとGPIOになる | 暗くなってから消すと、映像の平均の明るさが178→1になった |
| 明るさセンサー（フォトレジスター） | SAR ADCのチャネル1（`/dev/ADC_CH1`） | 明るいほど値が大きい | 夕方に285（16:06）から100（16:24）まで下がり、暗くなると14（赤外LEDの光だけ）になった |

- ADCのチャネル0（`/dev/ADC_CH0`）には何もつながっていない（10〜60でふらつく）。
- GPIO0_5は出力の1（Wi-FiとUSBの電源）。触らない。

## カメラ上の変更

| 場所 | 内容 | リポジトリ |
|---|---|---|
| `/etc/majestic.yaml` | 映像の `isp.blkCnt: 9` に `nightMode` を足した | [majestic.yaml](majestic.yaml) |
| `/etc/init.d/S94lightsensor`（755） | 起動のたびに、`/dev/ADC_CH0` を明るさセンサー（`ADC_CH1`）へのsymlinkにする | [S94lightsensor](S94lightsensor) |

`nightMode` の中身:

| キー | 値 | 意味 |
|---|---|---|
| `irCutPin1` | 2 | IRカットフィルターのGPIO |
| `irCutSingleInvert` | true | majesticの既定（1本駆動では昼が0）と逆なので反転する |
| `backlightPin` | 52 | 赤外LEDのGPIO。majesticは起動時にこのパッドをGPIOに切り替える（`0x200F0090` が0になる）ので、ピンの設定は別に要らない |
| `lightMonitor` | true | 自動の切り替え（Web画面の「Automatic day/night」） |
| `adcReadout` | true | 明るさをADCから読む |
| `adcInvert` | true | 明るいほど値が大きいセンサー |
| `minThreshold` | 100 | これを下回ると夜 |
| `maxThreshold` | 150 | これを上回ると昼 |

`irCut` と `backlight` は既定の `auto`（昼夜に合わせて動かす）、`colorToGray` は既定の `true`（夜は白黒）のまま。Web画面では、`minThreshold` が「Minimum sensor threshold for day」、`maxThreshold` が「Maximum sensor threshold for night」と表示されるが、`adcInvert: true` での動きは上の表のとおり（実機で確かめた）。

### 明るさセンサーをmajesticに読ませる

majesticのADC読み出し（`adcReadout`）は `/dev/ADC_CH0` しか開かない。この基板のセンサーはチャネル1にあるので、`S94lightsensor` がチャネル0のノードを `/dev/ADC_CH0.orig` に移し、`/dev/ADC_CH0` を `ADC_CH1` へのsymlinkにする。`/dev` はdevtmpfsで再起動すると元に戻るため、起動のたびに動かす。`open_adc` を読み込む `S70vendor` の後、majesticを起動する `S95majestic` の前に動き、`ADC_CH1` がなければ何もしない。majesticの `night_adc_raw` は、`/dev/ADC_CH1` を直接読んだ値と一致した。

## 切り替え方と設定

### Web画面

- 設定の「Day / Night」タブ（`http://<カメラ>/cgi-bin/camera.cgi?tab=nightMode`）で、自動の有無、しきい値、IRカットフィルターと赤外LEDの扱いを変えられる。保存すると、映像を止めずに夜モードの処理だけが再起動する。
- 自動が有効な間、プレビューには「Automatic day/night is driving night, IR‑cut and the lamp」と出て、手動のスイッチは出ない。自動（Automatic day/night）を切ると、プレビューに Night・IR‑cut・Lamp のスイッチが出る。

### API

rootのパスワードが要る（`/metrics` だけは要らない）。

| URL | 動き |
|---|---|
| `/night/on`、`/night/off`、`/night/toggle` | 夜にする、昼にする、入れ替える（フィルター、LED、白黒をまとめて） |
| `/night/ircut` | IRカットフィルターだけを入れ替える |
| `/night/light` | 赤外LEDだけを入れ替える |
| `/metrics` | `night_enabled`（1で夜）、`ircut_enabled`、`light_enabled`、`night_mode_source`（3でADC、0で手動）、`night_adc_raw`（センサーの値） |

自動が有効な間に `/night/on`・`/night/off` で切り替えても、センサーの値がしきい値の外にあれば、次の判定（5秒以内）で戻る。手動のままにしたいときは自動を切る。`/night/light` で消したLEDは、夜のままなら点け直されなかった。

```sh
curl -u root:<パスワード> http://<カメラ>/night/on
curl -s http://<カメラ>/metrics | grep -E '^night_(enabled|adc_raw) '
# しきい値を変える（/etc/majestic.yaml に保存され、夜モードの処理だけが再起動する）
curl -u root:<パスワード> -X POST -H 'Content-Type: application/json' \
  -d '{"nightMode":{"minThreshold":100,"maxThreshold":150}}' http://<カメラ>/api/v1/config
# 自動を切る（手動だけにする）
curl -u root:<パスワード> -X POST -H 'Content-Type: application/json' \
  -d '{"nightMode":{"lightMonitor":false}}' http://<カメラ>/api/v1/config
```

### しきい値の決め方

2026-11-16の夕方の記録（昼の間は赤外LEDが消えている）:

| 時刻 | センサーの値 | アナログゲイン | 状態 |
|---|---|---|---|
| 16:06 | 285 | 2.6倍 | 昼 |
| 16:16 | 210 | 4.5倍 | 昼 |
| 16:20 | 142 | 7.1倍 | 昼 |
| 16:23 | 103 | 10.4倍 | 昼 |
| 16:24 | 107 | 5.4倍 | 夜（100を下回って切り替わった直後） |
| 16:28 | 79 | 7.4倍 | 夜 |

- 夜に切り替わると、露出（`isp_exposure`）は9718から5032に下がった。赤外LEDと、フィルターを外したことで入る赤外のため。
- センサーは赤外LEDの光も拾う。値90前後のときは、LEDを消すと89、点けると98〜100だった。暗くなった17:00には、消すと0〜2、点けると14だった。
- そのため、`maxThreshold` と `minThreshold` の差は、LEDの分（10〜14）より十分に大きくする。差が小さいと、夜になってLEDが点いた途端に値が `maxThreshold` を超えて昼に戻り（朝はその逆）、行ったり来たりする。今の差は50。

## 実機での確認（2026-11-16）

- 手動: `/night/on` でgpio 2が0、gpio 52が1になり、露出は2454→902、彩度は0%（白黒）。`/night/off` で戻った。
- IRカットフィルターだけ: 外すとR/G比が1.108→1.187、露出が2337→905。
- 自動（しきい値を動かして）: センサーの値が245のとき、300/400にすると約6秒で夜、100/150にすると約5秒で昼になった。
- 自動（夕方）: 16:24、値が100を下回ったところで夜になった（ログは `Change mode to NIGHT`）。夜の映像は白黒（彩度0%）。
- 自動中の手動: 夜の間に `/night/off` で昼にすると、次の判定で夜に戻った（LEDが消えて値は89）。
- 暗くなってから（17:00、センサーの値14）: `/night/light` でLEDだけを消すと、映像の平均の明るさは178から1になり（ほぼ真っ黒）、センサーの値は0〜2になった。夜のままなので、自動はLEDを点け直さなかった（13秒見た）。もう一度 `/night/light` で点き、明るさは181に戻った。
- 再起動: ログに `lightsensor: ADC_CH0 now reads the light sensor (ADC_CH1)` が出て、`0x200F0090` は0、`night_mode_source` は3（ADC）。映像は今までどおり、`S96vicheck` が1回起動し直した後に出た。
- オフライン試験: [`tests/test_lightsensor.py`](../tests/test_lightsensor.py)。ホストのBusyBox ashで9つの場面を流す。

## 入れ方（SSHでrootになれる別の機体）

映像の設定（[../video/README.md](../video/README.md)）を済ませてから。

```sh
ssh root@<カメラ> 'cat > /etc/init.d/S94lightsensor && chmod 755 /etc/init.d/S94lightsensor' < S94lightsensor
ssh root@<カメラ> 'cat > /etc/majestic.yaml' < majestic.yaml   # 既存の設定があるなら nightMode だけ足す
ssh root@<カメラ> reboot
```

## 元に戻す

```sh
ssh root@<カメラ> 'rm /etc/init.d/S94lightsensor && cat > /etc/majestic.yaml' < ../video/majestic.yaml
ssh root@<カメラ> reboot
```

`nightMode` のピンがなければ、majesticはIRカットフィルターも赤外LEDも動かさない（この作業の前と同じ）。

## 残っていること

- しきい値は1回の夕方の記録から決めた。朝に昼へ戻るところはまだ見ていない。
- ADCで判定するときは `autoNightDelay` と `autoDayDelay` が効かないようで、値がしきい値を越えると次の判定（5秒以内）で切り替わる。夜に照明をつけるなどで値が150を超えると、すぐ昼に戻る。
- 時計は、自宅のルーターにつないでからNTPで合うようになった（[../wifi/README.md](../wifi/README.md)）。日の出と日の入り（`switchBy: sun`）や時刻での切り替えは、まだ試していない。
- `S94lightsensor` は、majesticがADCのチャネルを選べないための回避策。
