# 映像・音声

## 確認済みのRTMP接続先

```text
rtmp://192.168.10.34:1935/live/live
```

過去の `/tmp/iermu_play.log` で、app=`live`、stream=`live` の接続成功と再生開始、6.0秒間の映像54メッセージ・音声63メッセージの受信を確認しました。

メタデータで通知された値は次のとおりです。フレームレートやビットレートは実測値ではなく、カメラが通知したメタデータです。

| 項目 | 値 |
| --- | --- |
| 映像 | H.264/AVC |
| 解像度 | 1280 × 720 |
| 通知フレームレート | 10 fps |
| 音声 | AAC |
| 通知音声サンプルレート | 11025 Hz |
| 通知音声チャンネル | mono（stereo=false） |

この記録で確認したのは短時間の受信です。専用APへの移行後に長時間録画を完了した、という記録は本リポジトリに含めていません。

## 収録ツール

| ファイル | 機能 |
| --- | --- |
| `rtmp_probe.py` | Python標準ライブラリによるRTMP接続・再生・FLV出力のプローブ |
| `flv_dump.py` | FLVの映像タグとAVCCのNAL構造を表示 |
| `fix_flv.py` | 有効なNAL境界を使う映像ペイロード処理と、条件付きAAC末尾処理 |
| `iermu_rec.sh` | ffmpeg→FLV処理→MP4出力の補助パイプライン |

参照コードは `reference/iermu_cam/` にあります。

## プローブの使用形式

```bash
cd reference/iermu_cam
CAM=192.168.10.34 python3 rtmp_probe.py play live live 6 /tmp/iermu-sample.flv
python3 flv_dump.py /tmp/iermu-sample.flv
python3 fix_flv.py /tmp/iermu-sample.flv /tmp/iermu-normalized.flv truncate
```

映像・音声のファイルには私的な内容が含まれ得るため、Gitへ追加しません。

## FLV処理の範囲

`fix_flv.py` の `truncate` モードは、長さ付きNALを順に読み、有効なNAL境界より後に残るバイトを除きます。`extend` モードは最後のNALの長さフィールドへ残りを含めます。AACは、89バイトを除いた位置でコード内のEND要素検査を満たす場合に処理します。

付属テストでは、合成データによるNAL境界処理、FLVタグ長・PreviousTagSizeの整合性、AAC条件判定を確認します。これは全ファームウェア・全入力に対する互換性保証ではありません。
