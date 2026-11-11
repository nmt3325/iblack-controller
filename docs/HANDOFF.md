# 引継ぎと再現の範囲

## 場所

| 場所 | 用途 |
| --- | --- |
| `debian-02:/home/nmt3325/iermu-camera-local` | このGitリポジトリ。資料と参照コード |
| `debian-02:/home/nmt3325/iermu_cam` | 実運用のカメラ制御・受信コード |
| 同ディレクトリの `stab/` | 私用バックアップ、TLS関連、未加工ログ、検証ログ |
| `/etc/hostapd/`・`/etc/iermu-ap/` | 実PSK/MAC等を含む運用設定。Gitへコピーしない |
| `/usr/local/sbin/iermu-*` | AP/STAとRTMP publisherの運用スクリプト |
| `/etc/systemd/system/iermu-*` | 常駐ユニット |
| `/usr/local/bin/mediamtx` | 中継サーバーの導入済みバイナリ |
| `/home/nmt3325/diga-playback/ffmpeg` | 今回利用したFFmpegと共有ライブラリ |
| `oracle-ubuntu-03:/home/ubuntu/iermu` | APK/ELFの解析用原本と別ノードの視聴検証 |

Gitリポジトリでの編集・検査を、そのまま稼働コードの更新と考えない。今回の整理ではAPの切替・再起動・IR変更・スピーカー送信は行っていない。

## 普段の操作

通常の視聴は[RTMP視聴手順](TAILSCALE-RTMP.md)のURLを使う。

```bash
# 読み取り中心の状態確認
systemctl is-active iermu-wifi-vif iermu-ap iermu-dhcp \
  iermu-fakecloud iermu-sslcloud iermu-rtmp iermu-rtmp-publish
systemctl show iermu-rtmp-publish -p NRestarts -p PartOf
ss -ltn 'sport = :1935'
ip -4 route get 192.168.10.34

# 実運用ディレクトリでIRを操作する場合
cd /home/nmt3325/iermu_cam
python3 camctl.py status
python3 camctl.py night auto
# 必要なときのみ: night day / night night
```

IRのstatus/GETは現在モードを保証しない。[操作の制約](CONTROLS.md)を確認する。生ログや端末表示のMAC・識別子は共有前に除く。

### 直接受信の検証は中継と競合させない

常駐publisherがカメラのメディア接続を使う。通常の視聴では中継を利用し、直接RTMPの試験を重ねない。必要な試験だけpublisherを停止し、終了時に必ず再開する。IRのCMS制御は別ポートである。

```bash
sudo systemctl stop iermu-rtmp-publish
# この間だけ、必要な直接受信試験を実施
sudo systemctl start iermu-rtmp-publish
```

## 別環境へ移すために必要なもの

- Linux、Python 3、bash、systemd、hostapd、dnsmasq、iw、iproute2、iptables、NetworkManager、Tailscale。
- AP/managed同時動作が可能で、同一チャンネルで使える無線機。今回の構成に合わなければAP用とSTA用を物理的に分ける設計が必要。
- FFmpeg。今回の中継はH.264/AACをcopyし、通常の映像再エンコードはしない。
- MediaMTX v1.21.3。取得元・配布物名・SHA-256は[Tailscale検証JSON](../evidence/tailscale-rtmp-20261111.json)に記録。
- 旧TLS受信処理のPython依存は [requirements](../server/requirements.txt)。実際に使った環境のバージョンを記録している。
- アプリ静的解析は任意。[解析補助ツール](../research/README.md)を参照。
- PSK、実MAC、カメラのログイン情報、家庭用NetworkManagerプロファイル、TLS証明書と秘密鍵は各自の私用設定として用意する。
- 固定パス、Tailscale IP、無線名、経路・チャンネルを対象環境へ合わせる。`deployment/` は自動配備用インストーラーではない。

`client/camctl.py` の認証は `CAMERA_USER` と `CAMERA_PASSWORD` の環境変数、または既存実機の私用ファイルを使用する。資格情報の既定値は同梱しない。`.env.example` は項目一覧であり自動読込みされない。

## 検査と証拠

```bash
cd /home/nmt3325/iermu-camera-local
python3 tools/verify_repo.py
git diff --check
git status --short
```

意図したコード・資料変更後に現在版ハッシュを更新するときだけ `python3 tools/update_manifest.py` を実行し、その後に検査する。これは正しさや秘密情報の検査を代行しない。

初回参照版のハッシュは `evidence/source-manifest.json`、今回整理したファイルは `evidence/current-source-manifest.json`。21件のユニットテストと構文・リンク検査はオフラインで動く。実機検証とは別に扱う。

## 未完了の引継ぎ

優先順は、最新構成のカメラ電源再投入／ホスト再起動、長期連続運転、暗室IR、スピーカーの実音。DIGAは機器側の稼働・IPを別途確認する。完了条件は[全進捗](PROGRESS.md)にまとめた。

リモートは未設定。このローカルコミットをGitHub等へpushする場合は、公開範囲とリモート先を別途決める。Tailscale/LANアドレスやホスト名も公開したくない場合は、共有前に環境依存情報をさらに例示値へ置換する。
