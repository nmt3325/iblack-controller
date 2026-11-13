# iBlack 8MiBファームウェア原本・全展開物

ユーザー提供のフラッシュダンプ、展開ZIP、ZIPに含まれる全200ファイル、今回の静的解析資料を通常のGitオブジェクトとして保存する。Git LFSや外部アーカイブの取得は、このファームウェア一式には不要。

## 収録内容

| パス | 内容 |
| --- | --- |
| `original/iblack_1.bin` | 8,388,608バイトのフラッシュ原本。未改変 |
| `original/iblack_firmware_extracted.zip` | 提供された展開ZIP原本。未改変 |
| `original/firmware_analysis.md` | 提供された知見メモ原本。未改変 |
| `extracted/base_rootfs/` | BusyBox、uClibc、SysUpdate、起動スクリプト等 |
| `extracted/camera_modules/` | cmsMain.out、ドライバー、センサー関連等 |
| [analysis/README.md](analysis/README.md) | DNS・UART・3508更新サービス・更新形式・RTMPプッシュの追加解析 |
| `analysis/tools/` | 静的解析・DNS生成モデル・更新ヘッダー検査と22テスト |
| [manifest.json](manifest.json) / `SHA256SUMS` | 全収録ファイルのサイズ・SHA-256とZIP由来の対応 |

`.symlink` は提供ZIPに入っていたリンク先記述の通常ファイルのまま保存している。Gitへの収録時に実際のシンボリックリンクへ変換しない。原本・展開物は改行変換も行わず、Git保存後のバイト列も照合する。

## 整合性確認

リポジトリのルートから実行する。ネットワーク・カメラ・フラッシュへのアクセスはしない。

```sh
(cd firmware/iblack-8m && sha256sum -c SHA256SUMS)
python3 tools/verify_repo.py
# 解析テストには analysis/tools/requirements.txt のPython依存が必要
python3 -m unittest discover -s firmware/iblack-8m/analysis/tools -p 'test_*.py'
```

`iblack_1.bin` の正しいSHA-256は `b3e0ff253ed30696af53ecadb4ccf4e4220c6d6c6e38ce6730f4fe86e4a80da0`。提供メモには2桁欠けた値があるため、メモ原文は直さず、この値とmanifestを正とする。

フラッシュダンプとメーカーの更新パッケージは別形式。ここへの収録は、書き戻し・更新の成功、実機での全機能確認を意味しない。稼働中カメラの運用設定や配信サービスは変更していない。

## APKとの分離

ここにある `.so` / `.ko` / ELFはファームウェアZIP由来であり、Android APKから抽出したファイルではない。APK・DEX・Android側SO・JADX全文・JDK/JADX本体は、従来どおり[別アーカイブ](../../artifacts/README.md)に置く。
