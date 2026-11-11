# Tailscale経由のRTMP視聴

> 現在は通常Aterm AP構成へ移行済み。カメラは `.10.35`、DNS／クラウド受信先はLinux有線 `.1.201`。専用APは停止し、現行の手順・確認範囲は [通常AP移行](HOME-AP.md) を優先する。以下の専用AP構成の記述は旧構成・救済用の参照。

## 視聴URL

```text
rtmp://100.87.20.49:1935/live/camera
```

同じTailscaleへ接続した端末のVLCなどで、ネットワークストリームとして開く。カメラ用サブネットルートの追加承認は不要で、debian-02自身のTailscale IPv4へ接続する。Tailscale ACLで当該ホストのTCP 1935が許可されていることが前提。

H.264 1280×720と、カメラのマイクのAAC 11025Hz・モノラルを配信する。カメラ固有のNAL/AAC末尾を補正した後、再エンコードせずにRTMPへリマックスする。スピーカーへの送話を提供するURLではない。

## 構成

```text
camera RTMP -> rtmp_probe.py -> fix_flv.py -> FFmpeg stream copy
                                                |
                                                v
                      MediaMTX -> Tailscale TCP 1935 -> VLC等
```

- MediaMTXは公式v1.21.3のamd64リリースをチェックサム照合後に導入した。
- RTMPリスナーは `100.87.20.49:1935` にのみバインド。RTSP/HLS/WebRTC/SRT/API等は無効。
- 読取元はTailscale IPv4範囲、publish元はサーバー自身のTailscale IPv4だけに制限し、対象パスも `live/camera` に限定する。
- カメラへのRTMP接続は中継に集約する。視聴端末はカメラの生RTMPではなく、中継URLを使う。
- `iermu-rtmp.service` と `iermu-rtmp-publish.service` を永続化・自動起動。中継元が切断した場合、publisherのプロセスを再起動して接続を試し直す。視聴アプリ側では必要に応じてURLを開き直す。
- Tailscale再登録等でIPが変わった場合は、MediaMTX設定・publish先・許可元IPを更新する。

## 運用

```bash
systemctl status iermu-rtmp iermu-rtmp-publish --no-pager
sudo journalctl -u iermu-rtmp -u iermu-rtmp-publish -n 30 --no-pager
sudo systemctl restart iermu-rtmp-publish
```

直接カメラへ接続する `camera-stream` の手動テストを行う場合は、先に `sudo systemctl stop iermu-rtmp-publish`。テスト後は `sudo systemctl start iermu-rtmp-publish` で中継へ戻す。通常視聴でこの停止操作は不要。

## 確認範囲

debian-02上だけでなく、別のTailscaleノードから同じURLへ接続し、H.264映像とAAC音声の両ストリームを検出した。録画ファイルは作らず、復号検証はnull出力へ流す。ホスト再起動後の実動作は未検証。IRは別系統のCMS制御なので、既存の `camctl.py night auto|day|night` を継続使用する。


## Tailscale経由の受信検証結果

別のTailscaleノードからRTMPを受信し、映像とマイク音声を約12秒デコードできた（終了コード0、デコードエラー0）。録画ファイルは保存せず、null出力で検証した。
