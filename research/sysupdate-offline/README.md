# SysUpdate 自作更新のオフライン検証

[調査報告](../../docs/FIRMWARE-SELF-UPDATE.md)を先に読む。これは実際のARM命令をRAM上で動かす研究用コードであり、**ネットワーク送信ツールでも、起動可能な更新ファームでもない**。合成テストデータを実機へ送らないこと。確認した `0x96` は **appfs と bakfs の両方を上書きする**。

## 再現手順

リポジトリのルートで開始する。Python 3.11+ と `unicorn==2.1.4` を使用する。

```sh
REPO="$(pwd)"
VENV="$(mktemp -d)/venv"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install -r research/sysupdate-offline/requirements.txt
sha256sum -c research/sysupdate-offline/SHA256SUMS

# 生成JSONは一時ディレクトリへ出し、保存済みの証跡を上書きしない。
OUT="$(mktemp -d)"
cd "$OUT"
"$VENV/bin/python" "$REPO/research/sysupdate-offline/pack_update.py"
"$VENV/bin/python" "$REPO/research/sysupdate-offline/offline_write_proof.py"
"$VENV/bin/python" "$REPO/research/sysupdate-offline/offline_appfs_proof.py"
printf 'Generated reports: %s\n' "$OUT"
```

依存導入以外はオフライン。既に依存があればインストールも不要。カメラへの接続、実MTDの消去・書込み、ホストコマンドの実行、実再起動はしない。未知のインポートとARMシステムコールは拒否する。

既定の入力はこのリポジトリ内の [SysUpdate ELF](../../firmware/iblack-8m/extracted/base_rootfs/usr/bin/SysUpdate)。別パスを使う場合は `IERMU_FIRMWARE=/absolute/path/to/SysUpdate`、別ルートなら `IERMU_REPO=/absolute/path/to/repo` を指定する。ELFのSHA-256が `c76e218d9dffde035ca5ed28cae89fa2dd679c539f647f6329f8dcf083e0c6d9` と一致しなければ停止する。

## 内容と合格条件

- `firmware_math.py`：既存ハーネスから切り出したARM演算・I/Oスタブ。送信クライアントを含まない。
- `pack_update.py`：実ファーム内の暗号関数を使う、7サイズの更新形式・CRC往復検証。
- `offline_write_proof.py`：実 `sub_9498` と仮想MTDによる15ケース。正常系9件、異常系6件。
- `offline_appfs_proof.py`：実際のヘッダー受信・振分けと `0x96` による4ケース。正常系1件、異常系3件。
- [保存済み結果の概要](results/SUMMARY.json)：動的19件が成功、平文とappfs/bakfsのSHA-256が一致、ネットワーク・実デバイス書込み・ホストコマンドは0件。
- [書込み関数の証跡](results/offline_write_report.json)、[appfs更新経路の証跡](results/offline_appfs_report.json)、[追加往復検証](results/roundtrip.txt)。

`results/` は今回の保存済み証跡。`SUMMARY.json` の `repo_revision` は解析対象ELFを固定した元コミットで、後から追加した文書・コードのコミット番号ではない。JSONを再生成するときは上記のように別ディレクトリで実行する。

64 KiB消去ブロックはモデルの仮定で、実機測定値ではない。現行機での待受・到達性、物理フラッシュの挙動、最大サイズの起動可能イメージ、自作ファームの起動は未検証。再起動ヘルパーは入口で停止する。

通常のリポジトリQAはルートで `python3 tools/verify_repo.py`。上記19ケースは独立した追加検証であり、このQAへ自動登録したものではない。
