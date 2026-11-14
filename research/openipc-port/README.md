# OpenIPCネットワーク移行：オフライン検証

調査の判断と未解決事項は [`docs/OPENIPC-PORTING.md`](../../docs/OPENIPC-PORTING.md)。今後の調査文書・コード・機密除去済みJSONはGitに保存し、Notionにファイルを添付しない。

**このディレクトリに送信クライアントや実機フラッシュライターはない。全結果で `flash_write_allowed` は `false`。** 以前のネットワーク読出し調査は完了済みの別段階であり、今回のコマンドはカメラへ接続しない。

## ファイル

- `flash_layout.py`：uImage CRC、SquashFS位置、boot読出し範囲、旧MTD跨ぎ、env競合とoverlay容量を検査するdry-run。物理eraseサイズは未計測。
- `fullsize_appfs_proof.py`：既存Unicornハーネスで純正ARM受信・CRC・復号・`0x96`書込みコードを実行。仮想MTDのみ。容量上限+1 Bの危険な要求も厳格な境界で止めて記録する。
- `runtime_inventory.py`：公開kernelの自己展開gzipを検査し、公開rootfsから指定ファイルだけをホストの `unsquashfs -cat` で読み、split対応・Wi-Fi・RAM-root・watchdogの根拠を記録する。
- `tests/`：配置検査19ケース、runtime解析6ケース。合成データであり、起動可能性の検証ではない。
- `results/`：機密除去済みの観測結果。`passed` は明記された試験範囲だけを示し、移行許可ではない。
- `source-manifest.json`：入力のサイズ・SHA-256・公開URL／来歴上の限界。`latest`タグやreleaseの `target_commitish` はビルドソースの固定根拠にしない。

既存 `research/sysupdate-offline/pack_update.py` のopcode・画像サイズガードは変更していない。大容量envelopeは新しい検証関数のRAM内だけに存在し、エクスポート／保存／送信オプションはない。合成nonceも出力しない。元の仮想MTD境界検査も緩めていない。

## 再実行

リポジトリのルートで実行する。出力ファイルは上書きを拒否するため、一時ディレクトリへ保存する。

```sh
export PYTHONDONTWRITEBYTECODE=1
out=$(mktemp -d)
python3 -m unittest discover -s research/openipc-port/tests -v
python3 tools/verify_repo.py

python3 research/openipc-port/flash_layout.py \
  --firmware "$PUBLIC_ARTIFACTS/firmware.bin.hi3518ev100" \
  --bootloader "$PUBLIC_ARTIFACTS/u-boot-hi3518ev100-universal.bin" \
  --output "$out/flash-layout.json"

# 信頼できるホスト用unsquashfsを指定する。必要ならその依存ライブラリを
# LD_LIBRARY_PATHに設定する。rootfs内のバイナリはホスト上で実行しない。
python3 research/openipc-port/runtime_inventory.py \
  --kernel "$PUBLIC_ARTIFACTS/uImage.hi3518ev100" \
  --rootfs "$PUBLIC_ARTIFACTS/rootfs.squashfs.hi3518ev100" \
  --unsquashfs "$HOST_UNSQUASHFS" \
  --output "$out/runtime-inventory.json"

# 以下のみPython依存が必要：unicorn 2.1.4、capstone 5.0.3、pyelftools 0.32。
# 所有者保管の純正ELFと診断画像が必要。ダンプ／画像は配布しない。
python3 research/openipc-port/fullsize_appfs_proof.py \
  --firmware "$PRIVATE_INPUTS/SysUpdate" \
  --image "$PRIVATE_INPUTS/diagnostic-appfs.squashfs" \
  --output "$out/fullsize-appfs-proof.json"
```

`PUBLIC_ARTIFACTS` は説明用の一つのディレクトリを表す。今回のホストではsplitのcombined imageと単体kernel/rootfs、U-Bootは別ディレクトリに保管されており、実行時はそれぞれのパスを渡した。私的入力はハッシュまで一致する既存資材を使い、別画像への無条件な汎用更新ツールにはしていない。

### 観測された境界条件

`appfs` と `bakfs` が各2,621,440 Bで、bakfsに8 Bのヘッダが付くモデルでは最大画像は2,621,432 B。診断画像は2,609,152 Bなので12,280 B余る。2,621,433 Bでは純正コードが変更前に拒否せず、appfs更新とbakfs変更の後に、bakfs末尾から1 Bの書込みを要求した。検証器がこれを止めた。実機ドライバによる短い書込みやエラー時の最終状態は未検証。

## 結果の範囲

- `fullsize-appfs-proof.json` の `source_revision` は既存ハーネスのベースcommitを指す。新スクリプトの版はこのファイルを含むGit commitで識別する。
- `bakfs-readback-validation.json` は前段の7回の読出し調査から、許可した型・キーだけを抽出したもの。今回新たなカメラ通信は行っていない。当時のGit差分／接続状態を現在の状態と読み替えない。
- CRC正常・仮想更新成功・split対応文字列の存在は、電源断耐性、実機のSPI書込み／起動、USB接続、映像出力、watchdog引継ぎ、ピークRAMの保証ではない。
- 最初の実機への診断appfs投入自体が両アプリ領域の変更と再起動を伴う。別途の明示承認なしに行わない。

## Gitに入れないもの

元／読出しファームウェア、SquashFSやELF本体、個体設定・mdat、SSID／パスフレーズ、秘密鍵、認証値やそのハッシュ、カメラのセッション値、PCAP、暗号化更新envelope、私的ログ。`.gitignore`だけを安全策とせず、追加するテキストファイルを明示的に選び、内容を確認する。
