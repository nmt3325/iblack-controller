# 解析記録と補助ツール

## 用いた解析

- Android Java側：JADX 1.5.1。
- ARM/Thumbネイティブ側：pyelftoolsとCapstone。ELFのシンボル・PT_LOAD・PLTを使い、関数と文字列参照を読む。
- 入力：`com.intl.iermu`、versionName `1.8.0_1224`、versionCode 577。
- 原本は解析機の私用ディレクトリに置き、APK・共有ライブラリ・JDK/JADX本体はGitへ収録しない。
- [入力SHA-256と見つかったシンボル](../evidence/app-static-analysis.json)を保存した。

## 収録コード

`native/inspect_native.py` と `native/find_rtmp_xrefs.py` は今回使用した解析機のスクリプトを保存したもの。実行前提を残しているので、任意のELFに対応する汎用逆コンパイラーとは扱わない。

- `inspect_native.py`：ELFシンボルまたは `address:size` を指定し、ARM/Thumb逆アセンブルと参照先を表示する。
- `find_rtmp_xrefs.py`：カレントディレクトリの `lib/armeabi-v7a/libcmsffmpeg.so` を対象に、RTMP関連文字列への参照を探す。

依存ライブラリは [requirements](native/requirements.txt)。解析機で用いたバージョンを記録している。例：

```bash
# 自分の解析用環境に依存を用意したうえで実行
python3 research/native/inspect_native.py /path/to/libcmsplayer.so \
  iermuffp_start_audio iermuffp_stop_audio

# find_rtmp_xrefs.py は対象lib/を置いた作業ディレクトリから実行
python3 /path/to/repo/research/native/find_rtmp_xrefs.py
```

Pythonの通常のimport経路から依存を解決する。元スクリプトには私用 `retool/` を先に探す処理も残っているが、そのディレクトリは同梱しない。どちらも静的ファイル解析で、カメラへは接続しない。

## 分かったことと未解決

- 音声の開始・停止、FLVラップ、RTMP publish関連処理を追跡できた。
- カメラからのAAC受信とスピーカーへの音声送信は別経路・別の合格条件。
- RTMPの接続・ストリーム作成成功だけではスピーカー成功ではない。publish開始成功と実際に聞こえる音が必要。
- LAN2の定数を単純に切り詰めた試行、パラメータ39のトークン取得は対象機で成功していない。
- スピーカーへ音を送る実験スクリプトは、誤実行を避けるため通常ツールへ混在させていない。結果と残課題は[全進捗](../docs/PROGRESS.md)に記録する。

詳細な使い分けは[手法](../docs/METHODS.md)と[制御プロトコル](../docs/PROTOCOL.md)を参照。


## アプリ全操作の索引を再生成する

既存のJADX展開ツリーを入力にするだけで、ネットワーク・カメラ・アカウントにはアクセスしない。APK/native実行やクラウド送信もしない。

```bash
python3 research/index_app_operations.py \
  --source-root /path/to/jadx/sources \
  --output /tmp/app-operations-index.json
python3 research/build_app_operations_report.py \
  --source-root /path/to/jadx/sources \
  --index /tmp/app-operations-index.json \
  --output-root /tmp/app-operations-report
```

入力ハッシュが必要なら生成時に`--apk /path/to/app.apk --native-root /path/to/native-libs`も指定できる。操作解説本文は人が実装を追跡した資料で、自動付録とは区別する。[調査本文](../docs/APP-OPERATIONS.md)と[機械可読索引](../evidence/app-operations-index.json)を参照。字句索引であり、Javaの型解析・native/server側の完全解析ではない。
