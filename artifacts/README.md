# 別保管の解析原本・ツールチェーン

APK関連は外部保管とし、Git本体には制御コード、解析レポート、静的解析スクリプト、テストと検証メタデータを置く。ユーザー指定の[ファームウェア原本・展開ZIP・全展開物](../firmware/iblack-8m/README.md)は例外として通常Gitに収録する。APK由来の原本・抽出物・Java全文、およびJADX/JDKの大きな配布物は、通常のアーカイブとしてSeafileに分離した。Git本体にも別アーカイブにもGit LFSは不要。

## 取得先と整合性

- [Seafileの解析原本アーカイブ](https://sea.salvare.me/seafdav/NotionAI/iermu-camera/iermu-camera-analysis-artifacts-5ce4fb0.tar.gz)（設定済みの私有ライブラリへのログインが必要）
- [アーカイブ名・容量・SHA-256](archive.json)
- [原本ごとのSHA-256とJavaツリーハッシュ](manifest.json)

アーカイブのSHA-256: `a620dda7e5120e6c4e21bfb79b6b62d19c0ab18230be88f950e7afff2b9c349c`

## 収録範囲

| 内容 | 外部アーカイブ内のパス |
| --- | --- |
| APK原本 | `artifacts/apk/` |
| 全DEX（1ファイル） | `artifacts/dex/` |
| 全SO（8ファイル） | `artifacts/native/` |
| Java全文（4,206ファイル） | `research/decompiled/iermu/sources/` |
| リソースを含むJADX全出力（8,163ファイル） | `artifacts/jadx-output/` 内の tar.zst |
| JADX 1.5.1配布原本 | `artifacts/toolchains/jadx-1.5.1.zip` |
| Temurin JDK 21.0.13+9配布原本 | `artifacts/toolchains/temurin-21.0.13+9-linux-aarch64.tar.gz` |

JDK原本はLinux ARM64版で、x86_64上ではそのまま実行できない。原本はすべてバイト単位で保存し、JADXの失敗コメント・スタブも保持する。ビルド可能なAndroid開発プロジェクトの復元を意味しない。

## 展開と検証

SeafileからGitリポジトリの外へダウンロードし、展開する。認証情報をリポジトリやコマンド履歴へ保存しない。

```sh
# リポジトリの隣に展開した例
cd ..
sha256sum -c iermu-camera-analysis-artifacts-5ce4fb0.tar.gz.sha256
tar -xzf iermu-camera-analysis-artifacts-5ce4fb0.tar.gz
cd iermu-camera-local
python3 tools/verify_repo.py
python3 tools/verify_artifacts.py --root ../iermu-camera-analysis-artifacts-5ce4fb0
# Git本体と原本をまとめて検証する場合
python3 tools/verify_repo.py --artifacts-root ../iermu-camera-analysis-artifacts-5ce4fb0
```

通常の `python3 tools/verify_repo.py` は原本を要求せず、Git本体の構文・参照ハッシュ・リンク・メタデータ・89件のテストを検証する。原本検証を指定した場合だけ全バイナリとJava全文を照合し、APK内の全DEX/SOと抽出物の一致も確認する。ネットワークやカメラの操作は行わない。

## 静的操作索引の再生成

```sh
mkdir -p local
python3 research/index_app_operations.py \
  --source-root ../iermu-camera-analysis-artifacts-5ce4fb0/research/decompiled/iermu/sources \
  --output local/app-operations-index.json
```

## JADXの再実行例（Linux ARM64）

```sh
artifacts_root=../iermu-camera-analysis-artifacts-5ce4fb0
mkdir -p local/jadx local/jdk
unzip "$artifacts_root/artifacts/toolchains/jadx-1.5.1.zip" -d local/jadx
tar -xzf "$artifacts_root/artifacts/toolchains/temurin-21.0.13+9-linux-aarch64.tar.gz" --strip-components=1 -C local/jdk
JAVA_HOME="$PWD/local/jdk" PATH="$PWD/local/jdk/bin:$PATH" \
  local/jadx/bin/jadx -d local/jadx-regenerated "$artifacts_root/artifacts/apk/com.intl.iermu_1.8.0_1224_577.apk"
```

## 履歴の扱い

LFS追加前のコミット `83dc989` までの変更内容・コミット順序を引き継ぎ、その上に今回の分離構成を記録する。公開前に古い証跡のSSIDを例示値へ置換したため、影響するコミットSHAは再計算している。LFSを追加した旧コミット `5ce4fb0` を含むリポジトリはローカルに丸ごと退避している。軽量Gitの到達可能な履歴には、LFSポインタやAPK由来の原本を持ち込まない。分離作業直後は外部Gitサーバーへのpushや強制pushを行っていなかった。その後、ユーザー指定のGitHubリポジトリで通常Git版を共有している。
