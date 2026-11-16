# OpenIPCオンライン移行（純正U-Bootを残す方式）：実装とオフライン検証

判断と実機上の未解決事項は [`docs/OPENIPC-PORTING.md`](../../docs/OPENIPC-PORTING.md)、純正更新経路の前段検証は [`research/openipc-port`](../openipc-port/README.md) にある。

**恒久移行（`final`）は2026-11-15に実行したが、完了していない。** 5回目の試験起動（16/16項目合格）の後にネットワーク経由でfinalを始め、mdat1/mdat2の退避と後戻りできない点（PONR）の通過までは報告が届いた。その32〜36秒後にカメラの電波が止まり、以後の報告・接続はない。PONR後の書込み途中でハードウェアwatchdogがリセットしたとみられ、カメラは起動できない（ネットワークでは戻せず、SPIで復旧する）。詳細は「5回目の試験起動と本番移行の結果」の節。

それまでの経緯：当初の検証結果はオフライン試験であり、その `flash_write_allowed: false` は実機の操作履歴を表さない。その後、所有者の段階別承認のもと、移行appfsの配信、読取り専用診断、純正U-Bootの `saveenv` probe、OpenIPC kernel＋純正ユーザーランドの一度限りの試験起動を実施した。最初の試験ではkernel起動・Wi-Fiモジュール読込み・インターフェース出現を確認したが、DHCP完了前に再起動して純正APへ戻り、ネットワーク試験は不合格だった。実機操作は各段階の承認範囲だけで行い（後に所有者は段階ごとの承認を不要とした）、自動再試行しない。

### 試験起動の再起動対策

通常のOpenIPC起動処理 `load_hisilicon` と同じ公開rootfsの `wdt.ko` を試験payloadにも含め、カーネル内の給餌を明示的に有効にしてWi-Fi処理より前に読み込む。ドライバーの読込みに失敗したら試験を中止する。約1分の再起動と、以前のpayloadにこのドライバーがなかった事実は整合する。追加実機試験では起動175秒時点まで処理が続き、以前の途中再起動は再現しなかった。ただし、ハードウェアのリセット要因を直接測定したわけではないため、原因確定とは扱わない。レジスターへの直接書込みや不明なwatchdogの無効化は行わない。

DHCPは最大8回で、各開始前にSSID・PSKを含まない診断をtlへ保存する。ホストへの到達後も70秒間同じkernelで待ち、watchdog読込みと安定待機の両方の証拠がなければ、collectは試験を合格にしない。独立した900秒の純正復帰タイマーと、試験時の書込み範囲（tl・必要時の純正env）は変更しない。

修正版のオフライン検証（173 tests、ARM実バイナリの9項目）は [`results/trial-watchdog-offline-qa.json`](results/trial-watchdog-offline-qa.json) に記録した。この結果は追加の実機試験を合格とするものではない。

追加実機試験は [`results/trial-watchdog-hardware.json`](results/trial-watchdog-hardware.json) に記録した。OpenIPC kernel・watchdog・Wi-Fiモジュールの読込みとインターフェースupは成功したが、DHCPは8回とも失敗し、`no-dhcp-lease` で終了して純正APへ復帰した（11/14項目合格、ネットワーク到達後の70秒待機は未実行）。通常のOpenIPCは `wpa_supplicant -D nl80211,wext` を起動する一方、今回の試験は.datの設定とモジュール読込みだけで認証プロセスを起動していない。この差とAPへの関連付け・認証を次に切り分ける。単なるDHCPサーバー不調とは断定しない。この記録の時点では、それ以上の実機再試行や恒久移行は行っていない。

### 試験起動のWi-Fi認証処理

試験payloadで `wpa_supplicant 2.11` をまず `-D nl80211` で起動し、固定の認証完了イベントを受け取ってからDHCPへ進む。待機は最大90秒。そのプロセスが自分で終了したか、待機切れの後に停止させて終了を確認できた場合に限り、`-D wext` で1回だけ（最大60秒）試す。2つの認証プロセスを同時には動かさない。tlには試行ごとの結果（`auth`・`auth_wext`）と終了コード（`auth_rc`・`auth_wext_rc`。255は起動時の初期化失敗、128+NはシグナルNによる終了）を残す。DHCPは従来どおり最大8回とする。認証プロセスの終了、認証拒否、待機切れ、バイナリ検証失敗は成功扱いにしない。ホスト到達後の70秒待機中もプロセスと接続を再確認し、報告送信後に自分の認証プロセスだけを終了する。

通常のOpenIPCバイナリを追加すると704 KiBの試験領域を超えるため、固定した公開ソースから静的ARMv5 soft-floatのWPA2-PSK/CCMP専用ランタイムを作る。EAP・WPS・P2P・SAE・制御ソケットは含めないが、暗号処理とエントロピー取得は省略しない。ビルド方法と制約は [`auth/README.md`](auth/README.md)。この試験用ランタイムは恒久移行後のOpenIPC内蔵版とは別であり、後者の実機動作を保証しない。

SSIDは16進表現、PSKはホスト側で導出した値を私的な `wpa.conf` に入れる。これは依然として機密情報であり、試験payloadとともにbakfsに残る。生の認証ログはRAMだけに保持し、tl・アップロードには固定した5種類のイベントに由来する状態だけを記録する。上流Makefileによるイベント削除も回帰検査し、実際にランタイムを作ったオブジェクトをARM実行して、引数や認証情報が出ないことを確かめる。

最大長の仮SSID・PSKで、watchdogと認証ランタイムを含む試験用SquashFSは **688,128 / 720,896 B（余裕32,768 B）**。認証ランタイムは378,556 Bで、ARM926での起動・両ドライバー・固定イベントの6項目をオフライン確認した。8 MiBの配置、kernel領域、純正U-Boot、独立した900秒の純正復帰タイマーは変えない。オフライン合格を実機合格とは扱わず、実機操作は所有者が承認した1回ごとの範囲に限る。

認証修正版のオフライン検証は単体・結合189件、純正ARMの9項目、認証ランタイムARM926の6項目、隣接テスト25件がPASSした。リポジトリ検証は110件（既存の6 skip）で終了コード0。記録は [`results/trial-auth-offline-qa.json`](results/trial-auth-offline-qa.json)。実機の接続成功や恒久移行を示す記録ではない。

### 3回目の実機試験（認証）の結果と修正

3回目の実機試験は [`results/auth-trial-hardware.json`](results/auth-trial-hardware.json) に記録した。kernel・watchdog・Wi-Fiモジュール・インターフェース・認証ランタイムの照合までは合格したが、認証プロセスが固定イベントを1件も出さずに終了し、`wifi-auth-process-exit` で純正APへ戻った（DHCPと安定待機は未実行）。tlでは起動9.37秒に開始時刻の照合を通って開始を記録し、最初の生存確認（認証プロセスの開始から1秒未満）で終了を検出している。当初の記録にあった「直後の `/proc` 照合でプロセスを識別できなかった」は誤りなので、同じファイルで訂正した。

オフラインでは、同じ実行ファイルと試験payloadの `wpa.conf` をqemu-user（arm926）で起動すると、0.03秒で終了コード255になり、ソケットもドライバーも開かなかった。空の設定なら動き続ける。設定を1行ずつ外すと、`update_config=0` を外したときだけ動き続け、ほかのどの行を外しても255のままだった。SSID・PSKを仮の値に替えても結果は同じである。このランタイムは `CONFIG_NO_CONFIG_WRITE=y` でビルドしており、wpa_supplicant 2.11ではこの設定で `update_config` が解析表から外れる。未知のグローバル項目として設定の読込みに失敗し、`CONFIG_NO_STDOUT_DEBUG` のためエラー文も出なかった。これが**ほぼ確実な原因（実機の終了コードは未取得）**である。解析の記録は [`results/auth-exit-offline-analysis.json`](results/auth-exit-offline-analysis.json)。

修正内容:

- `wpa.conf` から `update_config` を外し、このランタイムが解析する項目（`ap_scan` と、RSN/WPA2-PSK/CCMPの `network` ブロック）だけにした。項目の一覧は単体テストで固定した。
- `OM_AUTH_RUNTIME`（私的なランタイムのディレクトリ）と `OM_QEMU_ARM`（`-cpu arm926` で起動するqemu-armのラッパー）を指定すると、実ランタイムを `lo` だけのネットワーク名前空間で起動し、生成した設定では動き続け、旧設定では255で終わることもテストする。
- 認証は `-D nl80211` で最大90秒待ち、そのプロセスの終了を確認できた場合だけ `-D wext` で1回（最大60秒）試す。終了を確認できなければwextは試さない。
- 試行ごとの結果と終了コードを `auth`・`auth_rc`・`auth_wext`・`auth_wext_rc` としてtlに残し、collectも出力する。wextで接続した場合も認証の証拠として扱う。

修正後のオフライン検証では、移行ツールの単体・結合テスト193件、隣接テスト25件、リポジトリ全体の検証（`tools/verify_repo.py`。110件中6件は任意の依存がないためスキップ）、純正BusyBox 1.18.4を使うqemu-armの試験チェーン9項目（実バイナリのe2eテスト16件、`trial.sh` の失敗・切替経路を含む）にすべて合格した。最大長の仮の認証情報で作った試験payloadは688,128バイト（上限720,896バイト、余裕32,768バイト）で、`trial.sh` は13,530バイトである。

修正後のソースから4回目用の試験イメージ（bakfs 2,621,440バイトと書込み用env 131,072バイト）をオフラインで作り直し、30項目の検証に合格した。kernelは1,864,732/1,900,544バイト、payloadは688,128/720,896バイト、bootcmdは417/1,023文字。展開したpayloadの `wpa.conf` に `update_config` がなく項目がランタイムの解析対象だけであること、展開した実行ファイルがその設定でqemu上で動き続けること、`trial.sh` が2段階の認証になっていることも確かめた。このイメージはカメラへ書き込んでいない。書き込む前には読み取り専用でenvとU-Bootを確認し直し、作成時の値と異なれば中止する。

qemu-userはnl80211（NETLINK_GENERIC）とwireless extensionsのioctlを実際のドライバーへ渡せないため、ドライバー初期化・スキャン・関連付け・4-way handshakeはオフラインでは検証できない。確認できたのは設定の受理と起動の継続までで、実機での認証成功は次の実機試験でしか確かめられない。4回目の試験は所有者の承認を得て行った（次節）。

### 4回目の実機試験の結果と修正

4回目の実機試験（認証設定の修正版）は [`results/auth-connected-event-analysis.json`](results/auth-connected-event-analysis.json) に記録した。kernel・watchdog・Wi-Fiモジュール・インターフェース・認証ランタイムの照合は合格し、認証プロセスは待機中に終了しなかった。ホストAPのログでは、nl80211（起動9.36秒に開始）でもwext（107.83秒に開始）でも、開始から約4秒で4-way handshakeが完了し、局は認証済みと表示された。それでも試験は `CTRL-EVENT-CONNECTED` を受け取れず、両方とも待機切れ（停止時の終了コード0）となり、`wifi-auth-failed` で純正APへ戻った。DHCPは始めていない（16項目中12項目合格）。U-Bootと純正envは変わっていない。

原因は試験用ランタイムのビルド構成にあった。wpa_supplicant 2.11が接続完了を出す `wpa_msg` の呼出しは1か所だけで、`#if defined(CONFIG_CTRL_IFACE) || !defined(CONFIG_NO_STDOUT_DEBUG)` で囲まれている。制御ソケットを持たず `CONFIG_NO_STDOUT_DEBUG` を指定したこのビルドでは、この呼出しがコンパイルされない。イベント名はパッチの固定イベント表に残るので、名前の有無だけを見る従来の検査では見つからなかった。qemu-userでは関連付けまで進めないため、オフラインのARM試験でも検出できなかった。

修正内容:

- `auth/event-only.patch` に3つ目のhunkを加え、試験専用マクロでもこの呼出しをコンパイルする。
- `trial_auth.py` は、各イベント名の直後に空白がある文字列（上流の書式文字列の先頭）も要求し、呼出しのないランタイムを拒否する。4回目のランタイムはこの検査で拒否される。
- 回帰テストを2件追加した。

Linuxホストで `mac80211_hwsim` を使って確かめた（root、専用のネットワーク名前空間、hostapd 2.10のWPA2-PSK/CCMP）。同じソース・パッチ・設定のネイティブビルドを使っている。4回目のパッチでは、APが4-way handshakeを完了したのにイベントが出ず、両バックエンドとも待機切れになり、4回目を再現した。修正後のパッチでは両方とも3回目のポーリングで接続を報告し、BusyBox `udhcpc` と `trial_dhcp.sh` によるDHCP、ping、接続の再確認も通った。確かめたのはサプリカントとカーネルのcfg80211/mac80211の経路で、ARMビルドとMT7601の実ドライバーは実機でしか確かめられない。修正後のARMランタイムは378,556 Bで、ビルダーの検査に合格した。

所有者は以後の実機試験とネットワーク経由の移行を、段階ごとの承認なしで行うことを認めた。書込み前の読取り専用確認、U-Bootを書かないこと、不一致時の中止は引き続き守る。

### 5回目の試験起動と本番移行の結果

5回目の試験起動（4回目の修正版、`28c6051`）は [`results/trial5-hardware.json`](results/trial5-hardware.json) に記録した。OpenIPC kernelがWi-Fiの認証、DHCP、ホストへの到達まで進み、16項目すべてに合格して純正APへ戻った。

続けて同じリビジョンでfinalを実行した（[`results/final-migration.json`](results/final-migration.json)）。時刻はUTC。17:17:27にaction開始、17:17:39にmdat1/mdat2の退避をホストが照合し、17:17:40にPONRの報告が届いた。17:18:12〜16に純正APのビーコンが消え、その後はfinal-verified・final-degraded・rebooting-finalのどれも届いていない。純正APはカーネル内で動き、再起動するまで残るはずなので、カメラは書込みの途中で止まったことになる。完了後のOpenIPCが参加するホストAPを30分立てたが、関連付け・認証・DHCPは0件。チャネル6を40秒監視しても、カメラの無線MACと純正BSSIDからのフレームは1つもなかった。

原因はハードウェアwatchdogとみている。

- 純正U-Boot 2010.06は `wdstart`/`wdstop` コマンドとwatchdog（`0x20040000`、ロック解除値 `0x1ACCE551`）の定数を持ち、Linuxより前にwatchdogを動かす。
- 1回目の試験起動（`wdt.ko` なし）は起動の約1分後に再起動し、`wdt.ko` を読み込んだ2回目以降は再現しなかった。Linuxより前からwatchdogが動いていることと整合する。
- 純正には `/dev/watchdog` がない（diagnose）。`cmsMain.out` は `/dev/mem` を開き、watchdogの基底アドレス（7か所）とロック解除値（3か所）を持つ。ユーザー空間から給餌している。
- 純正の `/usr/bin/wdstop`（シンボル付き）は、`cmsdlWdInit`（`/dev/mem` の `0x20040000` をmmap）の後に `cmsdlWdStop`（`+0xC00` に `0x1ACCE551`、`+0x008`（WdogControl）に0、`+0xC00` に0）を行うだけである。
- finalの `quiesce all` は、action開始直後（17:17:28ごろ）に `cmsMain.out` を含む全プロセスをSIGSTOPした。ビーコンが消えたのはその約44秒後。書込み計画は、PONR後にさらに約60秒（試験起動の実測は40ブロックで21秒）と照合を要していた。
- 試験起動のquiesce（`cmsMain.out` と `SysUpdate`）は約28秒で終わり、この周期に届かなかった。diagnoseの「watchdogデバイスがない」という確認では、ユーザー空間の給餌を見つけられなかった。

推定されるフラッシュの状態：U-Bootは不変、純正env（`0x80000` を読むbootcmd）のまま。`0x060000` のOpenIPC envと `0x070000`〜`0x1FFFFF` のOpenIPC kernelは書かれ、純正kernelは上書き済み。bakfsは書換えの途中で、mtd1〜mtd4は純正のまま。U-Bootの `bootm` はイメージを見つけられず、U-Boot自身が動かしたwatchdogでリセットを繰り返す。U-Bootにはシリアル・USB・TFTPしかなく、UARTもEthernetもないこの個体はネットワークでは戻せない。U-Bootは書いていない。

#### SPIでの復旧

8 MiBの全体を書き、読み戻して照合する。

- 純正へ戻す：元のダンプをそのまま書く。U-BootのSHA-256は実機で照合したピンと一致し、mdat1/mdat2はPONR直前に退避したものとバイト単位で一致した。純正envも、キーと値は実機のenvと同じ（試験起動のsaveenvでキーの順序だけが変わっていた）。
- OpenIPCを直接書く：`compose_spi_image.py openipc` で、ダンプのU-Boot、`env-final.img`、`env-openipc.img`、`firmware.bin`、消去済みのrootfs_dataを組む。インストーラーの書込み計画をダンプ上で再生した結果とバイト単位で一致することも確かめる。env-finalでの起動経路そのものは、実機で動かしていない。起動後はenv-openipcのSSIDへ参加するので、ホストAPを立てておく。

出力は0600で作り、上書きしない。MACアドレスやWi-Fiの鍵を含むので、Gitには入れない。合成ツールの単体テストは `tests/test_compose_spi_image.py`（合成データだけを使う）。

#### watchdog対策（実装済み、オフライン検証のみ）

1台目の結果から必要とした対策を実装した。検証は「watchdog対策の検証（オフライン）」にある。

- **停止**：`migrate_hook.sh` は `quiesce` の直後に、RAM上のOpenIPC BusyBoxの `devmem` で、純正 `wdstop` と同じ手順（`+0xC00` に解除値、WdogControlに0、再ロック）でSP805を止める。止める前のWdogControlを保存し、読み戻して止まっていなければ、止めたプロセスを再開して中止する。「レジスターへの直接書込みは行わない」という前述の方針は、この手順に限って改めた。
- **再始動**：`resume`（中止を含む）と、許可された再起動の直前に、保存した値でwatchdogを再始動する（`wdt-rearm`）。再起動が固まってもwatchdogがリセットする。
- **実機ゲート `wdtgate`**（書込みなし）：全プロセスを止めてwatchdogを停止した状態で300秒保ち、止めたままホストへ報告を送ってから再開する。`build_stockboot.py wdtgate` は、1回の起動の中で240秒以上保てたこと、停止中にwatchdogが止まっていたこと、停止中の報告が届いたこと、再開後に元の状態へ戻ったことを確かめる。finalは、試験起動とこのゲートの両方が通っていなければ作らない。
- **進捗**：finalはPONR後も区画ごとに進捗を送る（届かなくても続行する）。
- **diagnose**：SP805のレジスターが `devmem` で読めることも確かめる。純正U-Boot（`0x000000`–`0x03FFFF`）の読み戻しを `diag-uboot` として送り（任意）、ホストはカメラが読んだハッシュと照合する。U-Bootの末尾を伏せたダンプから復旧用のSPIイメージを組むときに使う。
- **ホスト**：final中にNetworkManagerが家庭用の接続へ自動で戻らないようにする（運用手順）。

## 前提と方式

対象の個体はWi-Fiしか使えない（UARTもEthernetもない）。SPIでの読み書きはでき、8 MiB全体のダンプがある。この前提で次のように決めた。

- **純正U-Boot（`0x000000`–`0x03FFFF`）は一度も書かない。** この基板で起動したことのないOpenIPC U-Bootを焼くと、起動しなかったときにSPI以外で戻る手段がない。純正U-Boot 2010.06は、今までどおり自分のenv（`0x040000`、128 KiB）を読む。そのenvの `bootcmd` でOpenIPCのkernelを起動する。
- OpenIPCは純正U-Bootの後ろにずらして置く（下の配置）。公開firmware `hi3518ev100-nor-lite-split`（`firmware.bin`、7,012,352 B）は、加工せずに `0x070000` へ書く。
- 本番の前に、**自動で純正へ戻る1回だけの試験起動**を行う。OpenIPCのkernelを純正bakfs領域から一度だけ起動し、純正のユーザーランドのままOpenIPCのWi-Fiドライバで接続して、LAN上のホストへ報告する。試験用envは、kernelを起動する**前に**純正の `bootcmd` を書き戻して `saveenv` する。kernelのCRC不良、panic、停止後の電源入れ直しのどれでも、次の起動は純正になる。
- その前に、純正U-Bootがこちらの用意したenvを実際に実行し、`saveenv` できることをprobeで確かめる。
- 最初に読取り専用のdiagnoseを行う。128ブロック全部を3回ずつ読んで安定性を見て、純正U-BootがSPIダンプと一致することも確かめる。

以前の実装（OpenIPC U-Bootを含めて8 MiB全体を書き換える方式、コミット `b66cf56`）は、UARTのないこの個体には向かないので削除した。`installer/openipc_migrate.sh`、`migrate_plan.py`、`tests/simulate_install.py`、`tests/test_migrate_plan.py`、`results/migrate-plan.json` が該当する。履歴には残っている。

## フラッシュ配置と容量

```text
純正          0x000000 2048K boot（U-Boot 256K、env 128K、kernel）  0x200000 768K rootfs  0x2C0000 2560K appfs
              0x540000 128K mdat1  0x560000 128K mdat2  0x580000 2560K bakfs
試験起動      純正の6区画（すべて読取り専用）に加えて
              tk 0x580000 1856K（OpenIPC kernel）  tp 0x750000 704K（試験用SquashFS）
              tl 0x070000 64K（試験ログ。純正では未使用の0xFF領域）  senv 0x040000 128K（純正env）
移行後        0x000000 384K boot(ro)（純正U-Boot＋純正env）  0x060000 64K env（OpenIPCのfw_printenv用）
              0x070000 1856K kernel  0x240000 4992K rootfs  0x720000 896K rootfs_data
```

移行後の `mtdparts` は `hi_sfc:384k(boot)ro,64k(env),1856k(kernel),4992k(rootfs),896k(rootfs_data)` で、合計8 MiB。`0x060000`–`0x07FFFF` は純正の配置では使われていない。

初期実装で固定した公開firmwareと所有者保管の入力から測った値（watchdog・認証追加前の履歴。現在の試験payload容量は上記を参照）（[`results/stockboot-plan.json`](results/stockboot-plan.json)）：

| 項目 | 使用 | 上限 | 余裕 |
| --- | --- | --- | --- |
| kernel（試験と本番で同じuImage） | 1,864,732 B | 1,900,544 B（1856 KiB） | 35,812 B |
| OpenIPC rootfs（SquashFS） | 5,100,346 B | 5,111,808 B（4992 KiB） | 11,462 B |
| 初期の試験用SquashFS（watchdog・認証追加前） | 520,192 B | 720,896 B（704 KiB） | 200,704 B |
| 移行appfs（`0x96` 経路） | 2,613,248 B | 2,621,432 B | 8,184 B |
| 試験用 `bootcmd`（純正U-Bootの `CONFIG_SYS_CBSIZE` 1024） | 417文字 | 1,023文字 | 606文字 |
| probe用 `bootcmd` | 158文字 | 1,023文字 | 865文字 |
| 移行後の `bootcmd`／`bootargs` | 69文字／174文字 | 1,023文字 | — |

試験用SquashFSは、SSID 32文字・パスフレーズ63文字（WPAの最大長）の仮の値で測った。kernelとrootfsの余裕が小さいので、将来の更新で別のfirmwareを書く場合は、同じ容量検査を必ず通す。

前段で読出し不一致が出た `0x6c4f28` は、純正ではbakfs+`0x144f28`、試験起動ではtk（OpenIPC kernel）+`0x144f28`、移行後はrootfs+`0x484f28`（使用中のSquashFSデータ内）に当たる。diagnoseはこのブロックを16回読み、1回でも違えば先へ進まない。試験起動は `verify=y` で起動するので、kernelをこの位置で読み違えればCRC不良で純正へ戻る。

## 流れ

```text
純正TCP3508の 0x96 で移行appfsを届ける（リポジトリ外の私的な送信ツール）：appfsとbakfsを書き換えて再起動
                                  ↓
純正firmwareが起動 → runapp.sh末尾の起動行 → stage2.sh phase1（純正sh）がホストのmanifestを確認
                                  ↓ run id、GO語句
phase2（RAM上のOpenIPC BusyBox）：manifestのseqが変わるたびに om_stockboot.sh を1回だけ実行
  1. diagnose     読取りのみ         → build_stockboot.py diag（SPIダンプと照合）
  2. wdtgate      書込みなし：全プロセス停止＋watchdog停止で300秒保持 → 再開
                  → build_stockboot.py wdtgate
  3. probe-arm    純正env ← probe env、再起動 → 純正U-Bootが純正bootcmdを書き戻してsaveenv → 純正で起動
                  → collect          → build_stockboot.py collect --stage probe
  4. trial-arm    bakfs ← 試験イメージ、最後に純正env ← 試験用env、再起動
                  → 純正U-BootがsaveenvしてからOpenIPC kernelを一度だけ起動
                  → trial.sh（純正ユーザーランド）がWi-Fiで接続してホストへ報告 → 再起動で純正へ
                  → collect          → build_stockboot.py collect --stage trial
  5. final        OpenIPC env → firmware → overlay消去 → 最後に純正env、再起動でOpenIPC
  （final前なら restore-env で、diagnose時に読んだ純正envへいつでも戻せる）
```

| ファイル | 役割 | 実行場所 |
| --- | --- | --- |
| `stock_layout.py` | 配置、純正env（128 KiB）の符号化、純正U-Boot 2010.06のコマンド解釈器とLinux 3.0.8 `cmdlinepart` のモデル、probe／試験／本番／復元envの生成と検査 | ホスト |
| `build_stockboot.py` | ホスト側の状態機械。diagnoseとcollectの結果を検査し、各段階の私的なaction一式を作る。前の段階が通っていなければ拒否する。`plan` は容量報告 | ホスト |
| `make_env.py` | OpenIPC U-Bootの既定envから、移行後の `0x060000` に置く64 KiBのenv画像（Wi-Fi設定入り）を作る。既定で `sensor=ar0141` と、届かない `upgrade` のURLも入れる | ホスト |
| `build_migration_appfs.py` | 純正appfsに `openipc-migrate/{stage2.sh,migrate.conf}`、起動行、試験起動の分岐を加えた移行appfsとbundleを作る | ホスト |
| `serve_migration.py` | bundleとaction一式だけを配るLAN側HTTPサーバ。1プロセス1モード | ホスト |
| `camera/stage2.sh` | カメラ側ドライバ。phase1は純正sh、phase2はRAM上のOpenIPC BusyBox | カメラ |
| `installer/om_stockboot.sh` | diagnose、collect、probe-arm、trial-arm、restore-env、finalの本体 | カメラ（phase2） |
| `camera/trial.sh`、`camera/trial_dhcp.sh` | 試験起動でのWi-Fi接続、DHCP、報告、`tl` へのログ記録 | カメラ（試験起動） |
| `camera/migrate_hook.sh` | 純正アプリの停止・再開、送信、進捗 | カメラ（phase2） |
| `validate_online_qemu.py` | 実ARMバイナリを `qemu-arm` で動かす検証 | ホスト |
| `easy/easy_migrate.py`、`easy/post_setup.py` | 移行の全段階を、止まっても再開できる1つのコマンドにしたものと、移行後の設定・更新ガードの導入（[easy/README.md](easy/README.md)） | ホスト（SSHでカメラ） |
| `update/sysupgrade-guard.sh`、`update/check_update.py` | 固定の区画を壊す更新を止めるガードと、更新が区画に収まるかの判定（[update/README.md](update/README.md)） | カメラ／ホスト |
| `tests/` | ユニット、模擬フラッシュでのインストーラー、E2E | ホスト |

**移行appfsを届ける部分（純正TCP3508の `0x96` 更新）はリポジトリに入れていない。** 実機では、所有者の承認のもとリポジトリ外の私的な送信ツールを使った。既存の `research/sysupdate-offline/pack_update.py` のガードは変えていない。`0x96` はappfsとbakfsを両方書き換えて再起動する。trial-armの後はbakfsに純正appfsの控えが残らない。そこから先の復旧手段はSPIダンプになる。

## 運用順（実機で行う場合）

各段階で、ホストは1モードだけの `serve_migration.py` を起動する。カメラは起動ごとにphase1を1回だけ始める（起動45秒後から20秒ごと、最大30分）。間に合わなければ電源を入れ直す。私的なディレクトリ（`$ST` 状態、`$UP` 受信物、`$A` action一式）はリポジトリの外に置き、0700にする。

```sh
python3 serve_migration.py --bundle "$B" --mode standby                       # 届くことだけ確認
python3 serve_migration.py --bundle "$B" --mode diagnose --backup-dir "$UP"   # 読取りのみ
python3 build_stockboot.py --state "$ST" diag --uploads "$UP" --spi-dump "$SPI_DUMP"
python3 serve_migration.py --bundle "$B" --mode wdtgate --backup-dir "$UP"    # 書込みなし。約5分止まる
python3 build_stockboot.py --state "$ST" wdtgate --uploads "$UP"
python3 build_stockboot.py --state "$ST" probe --out "$A/probe"
python3 serve_migration.py --bundle "$B" --mode probe-arm --action-dir "$A/probe" --backup-dir "$UP" \
  --write-confirm yes-arm-saveenv-probe --reboot-confirm yes-reboot-for-saveenv-probe
python3 serve_migration.py --bundle "$B" --mode collect --backup-dir "$UP"
python3 build_stockboot.py --state "$ST" collect --uploads "$UP" --stage probe
python3 build_stockboot.py --state "$ST" trial --firmware "$FW" --oipc-rootfs "$OIPC_ROOTFS" \
  --wifi-vars "$PRIVATE/wifi.vars" --auth-runtime "$AUTH_RUNTIME" --mksquashfs "$HOST_MKSQUASHFS" --out "$A/trial"
python3 serve_migration.py --bundle "$B" --mode trial-arm --action-dir "$A/trial" --backup-dir "$UP" \
  --write-confirm yes-arm-one-shot-openipc-trial --reboot-confirm yes-reboot-into-openipc-trial
#   （試験起動の報告もこのサーバが受ける。純正へ戻るまで止めない）
python3 serve_migration.py --bundle "$B" --mode collect --backup-dir "$UP"
python3 build_stockboot.py --state "$ST" collect --uploads "$UP" --stage trial
python3 build_stockboot.py --state "$ST" final --firmware "$FW" --out "$A/final"
python3 serve_migration.py --bundle "$B" --mode final --action-dir "$A/final" --backup-dir "$UP" \
  --write-confirm yes-migrate-this-camera-to-openipc --reboot-confirm yes-reboot-into-openipc
```

途中でやめるときは `build_stockboot.py restore` と `serve_migration.py --mode restore-env --write-confirm yes-restore-stock-uboot-env` で、diagnose時の純正envへ戻す。

移行後は、OpenIPCの `sysupgrade -u`（U-Boot更新）を使わない。`boot` 区画は読取り専用にしてあるが、この区画には純正U-Bootと純正envがあり、ここを書くと起動しなくなる。kernelとrootfsの更新も、上の容量に収まるものに限る。標準のオンライン更新（Web UIのGitHubからの更新、`sysupgrade -k -r`、自動更新）は分割版の `firmware.bin` を `0x070000` から一続きに書いてこの配置を壊すので使わない。移行後のカメラには、これを止める更新ガードを入れる（[update/README.md](update/README.md)）。OpenIPC側の `fw_setenv` は `0x060000` のenvを書くだけで、純正U-Bootの起動設定は変わらない。

## 安全ゲート

- **run idと語句**：run idは既定でビルドごとに128ビットの乱数を作り、appfsに焼き込む。run idが合わない要求には何も配らない。phase1はmanifestに正確なGO語句がなければ何もしない。書込みには段階ごとの書込み語句（サーバ側は `--write-confirm`）が要り、再起動にはさらに再起動語句（`--reboot-confirm`）が要る。
- **1回だけ**：サーバは各actionを乱数のseqで1回だけ出し、カメラが開始を報告したら取り下げる。
- **ピン**：phase1は純正の `sha1sum` と `md5sum` の両方で照合し、phase2はSHA-256で照合する。action一式はmanifestが示すSHA-256で取得する。`firmware.bin` と `env-openipc.img` は、`migrate.conf` に焼き込んだピンでしか取得しない。サーバも起動時に全ファイルを再ハッシュし、action一式を `stock_layout.py` で検査し直してから、メモリ上の内容だけを配る。
- **ホスト側の順序**：`build_stockboot.py` は前の段階が通っていなければ次を作らない。diagnoseは、全ブロックの読出しが安定していること、16回読んだ弱いブロックが一致すること、純正U-BootがSPIダンプと一致すること、純正envが純正どおりであること、MTD表が純正の6区画であること、MT7601（`148f:7601`）があること、watchdogデバイスがないこと、SP805のレジスターが `devmem` で読めることを確かめる。finalは、試験起動とwdtgateの両方が通っていなければ作らない。試験起動は、nonce、マーカー付きの起動、MTD表、モジュールのロード、Wi-Fiインターフェース、DHCP、ホストへの到達、U-Bootの `saveenv`、純正env、U-Bootの不変、読出しの安定をすべて確かめる。
- **書く前の照合**：書込みactionごとに、純正U-BootのSHA-256がdiagnose時の値と一致することを、書く前と書いた後に確かめる。純正envが、ホストがactionを作ったときのenvと一致しなければ書かない。
- **書込みと照合**：mtd1–mtd5は区画全体を消去してcharデバイスへ書く。mtd0（env）は64 KiBごとに `mtdblock0` 経由で書き、ほかのプロセスが `mtdblock0` を開いていないときだけ書く。照合は、ページキャッシュを避けるためMTDのcharデバイスを最大3回読み、2回一致で判定する。
- **書く順序**：trial-armはbakfs、最後に純正env。finalは `0x060000` のenvと `0x070000` の先頭ブロック（ここまでは純正が使わない領域で、中止しても純正のまま）、次に `0x080000` 以降（ここから後戻りできない）、最後に純正env。後戻りできない点を越えたら中止せず、失敗したブロックを再試行して記録し、純正envは必ず最後に書く。その後の実行で再試行できる。
- **試験起動の分岐**：runapp.shの分岐は、`/proc/cmdline` に ` omtrial=1` があり、`/proc/mtd` に試験用の `tp` 行が正確にある場合だけ動く。分岐に入るとすぐ900秒のタイマーを起動し、時間が来たら `reboot -f` する。trial.shが書くのは `tl`（ログ）だけで、`saveenv` が失敗していた場合に限り純正envを書き戻す。Wi-Fiの設定は記録しない。
- **停止とwatchdog**：書く前に `cmsMain.out` と `SysUpdate` をSIGSTOPで止める（finalとwdtgateは全プロセス）。watchdogデバイスがあれば停止を拒否する。止めた直後にSP805を純正 `wdstop` と同じ手順で止め、読み戻して確かめる（止まらなければ再開して中止）。再開時と許可された再起動の直前に、元の状態へ戻す。以前の版はハードウェアwatchdogを止めなかったため、1台目のfinalは書込み途中でリセットされた（「5回目の試験起動と本番移行の結果」）。空きRAMの推定値が12 MiB以上、作業用の空きが10 MiB以上でなければ始めない。通常の容量管理されたファイルシステムでは `df` の空きを使う。純正のtiny-shmemカーネルは `/dev` をtmpfsとしてマウントしても実体がRAMFSで、`df` は総容量・空きとも0になる。`stat -f` の表示がRAMFS（純正BusyBoxでは誤って `romfs`）で全ブロック数0、かつ `/proc/mounts` で正規化した作業ディレクトリを最長一致する一意な `ramfs` / `tmpfs` が `rw`（`ro` なし）である場合だけ、空きRAM推定値から別途2 MiBを残した値を作業用の空きとして使う。RAMFSのページを再利用可能と誤算しないよう `Shmem` と `Unevictable` を引く（両項目がない場合は `MemFree + Buffers` のみ）。本当に満杯のtmpfs、未知の形式、不正な出力では緩和しない。
- **終了コード**：0は完了、1は純正U-Bootの起動対象を変えずに拒否・失敗、2は書込み途中で照合が終わっていない（電源を保ったままfinalを再試行するか、SPIで復旧）、3は照合済みだが許可された再起動が起きなかった。finalで後戻りできない点を越えた後は、stage2が終了せず、finalの再試行とcollectだけを受け付ける。
- テスト用のキー（`OM_SIM_*` など）は `render_conf(allow_test_keys=True)` のときだけ受け付ける。ビルダーはappfsに書かない。

## 電源断・失敗時

| 段階 | 途中で電源が切れたとき |
| --- | --- |
| probe-arm：env書込み（64 KiB×2） | envのCRCが不良 → 純正U-Bootの既定env（`mem=40M`、純正配置）→ 純正で起動 |
| probe起動：U-Bootの `saveenv` | 同上 |
| trial-arm：bakfs書込み | 純正で起動（bakfsには試験イメージしかない） |
| trial-arm：env書込み | envのCRCが不良 → 既定env → 純正で起動 |
| 試験起動：U-Bootの `saveenv` | 同上 |
| 試験kernelのCRC不良、panic、停止して電源入れ直し | 純正bootcmdは保存済み → 純正で起動 |
| final：`0x060000` のenvと `0x070000` の先頭ブロック | 純正のまま（純正が使わない領域） |
| final：`0x080000` 以降のfirmwareとoverlay消去 | 純正kernelが消え、envは純正のまま → U-Bootが止まる → SPIで復旧 |
| final：純正envの書込み（最後） | envのCRCが不良 → 既定env → 純正bootcmdがOpenIPCのデータを読む → SPIで復旧 |
| final完了後 | OpenIPCで起動（`verify=y`。CRC不良ならリセットを繰り返す → SPIで復旧） |

finalの後戻りできない区間は、firmware約6.6 MiBの消去・書込み・照合の間（試験起動の書込み速度から1〜2分の見込み）。この間は電源を切らない。2026-11-15のfinalは、この区間でwatchdogのリセットにより止まった。

## 検証結果（オフライン・エミュレーションだけ）

結果ファイル：[`results/validation-summary.json`](results/validation-summary.json)、[`results/online-qemu-validation.json`](results/online-qemu-validation.json)、[`results/stockboot-plan.json`](results/stockboot-plan.json)。

### x86ホスト

`python3 -m unittest discover -s tests -v`：**301件が成功**（失敗0、スキップ2、2026-11-16。実ツールの `mksquashfs`／`unsquashfs` あり、約4分）。スキップの2件は、私的な認証ランタイムとqemu-armが要る `test_trial_auth` の実ランタイム試験。最初にまとめたときは135件だった。

| モジュール | 件数 | 内容 |
| --- | --- | --- |
| `test_stock_layout` | 21 | 配置と容量、純正env（128 KiB）の符号化、純正U-Bootのコマンド解釈と `_do_setenv`、`cmdlinepart`、probe／試験／本番／復元env、`0x050000` にuImageヘッダがないこと |
| `test_build_stockboot` | 25 | 段階の順序、diagnoseとcollectの判定（不安定なブロック、16回読む弱いブロック、別個体のダンプ）、試験用SquashFSの容量とtlの雛形、純正BusyBoxで動かないスクリプトを試験用SquashFSに入れないこと、action一式 |
| `test_om_stockboot` | 12 | 模擬フラッシュ上の `om_stockboot.sh`：diagnoseとcollectは読取りのみ、probe-armは純正envだけを書く、語句と計画どおりのenvが要る、env書込みの失敗で元に戻す、試験イメージの失敗ではenvを書かない、finalの一時的な読み書き不良・後戻りできない点の前後の失敗・再試行 |
| `test_build_migration_appfs` | 37 | 移行appfsの組立てと容量、runapp.shの起動行と試験起動の分岐（純正shで実行）、stage2.shと `trial.sh` が純正BusyBoxの範囲に収まること（`dd` の引数、`grep -x` を含む）、bundle |
| `test_serve_migration` | 24 | bundleとaction一式の検査、モードごとのmanifest、1回だけのaction、run id、受信物（純正 `wget --post-data` の%XX形式の試験ログを元に戻すこと、受信を断るときも本文を読み切ってから応答すること）、語句の一致 |
| `test_make_env` | 17 | OpenIPC U-Bootの既定env抽出、許可キー、`0x060000` 用の配置、私的ファイルの扱い、envの `sensor`・`upgrade` の既定値 |
| `test_e2e_online` | 18 | phase1→phase2→installer→試験起動の `trial.sh` をHTTP越しに通す（Debian版BusyBox。`dd` と `grep` は純正と同じ引数だけ通し、`wget --post-data` は純正と同じ%XX形式で送る。模擬フラッシュ）。`tl` の中身、ホストが受け取った試験ログ、`saveenv` が失敗していた場合の純正envの書き戻しも確かめる |
| `test_phase1_capacity` | 23 | 純正shとawkでのRAMFS容量判定（デバイスに触れない） |
| `test_diagnose_liveness` | 8 | 読取り専用の診断が純正のサービスを止めないこと、大きな読出しの前のメタデータ確認、待機中の状態が見えること |
| `test_migrate_hook_wdt` | 10 | `migrate_hook.sh` とSP805 watchdog（下の「watchdog対策の検証（オフライン）」） |
| `test_trial_auth` | 20 | 試験起動だけのWPA2設定とARM実行環境の取り決め |
| `test_compose_spi_image` | 6 | `compose_spi_image.py`（合成したフラッシュ内容だけ） |
| `test_vicheck` | 8 | `video/S96vicheck`（[video/README.md](video/README.md)） |
| `test_lightsensor` | 9 | `night/S94lightsensor`（[night/README.md](night/README.md)） |
| `test_wifi_switch_trial` | 6 | `wifi/wifi-switch-trial.sh`（[wifi/README.md](wifi/README.md)） |
| `test_post_setup` | 13 | `easy/post_setup.py`（[easy/README.md](easy/README.md)） |
| `test_easy_migrate` | 15 | `easy/easy_migrate.py`（模擬カメラ相手。[easy/README.md](easy/README.md)） |
| `test_sysupgrade_guard` | 20 | `update/sysupgrade-guard.sh`（[update/README.md](update/README.md)） |
| `test_check_update` | 9 | `update/check_update.py`（[update/README.md](update/README.md)） |

### qemu-arm（ユーザーモード、`-cpu arm926`）

純正rootfsのuClibc版BusyBox 1.18.4と、OpenIPC firmware内のmusl版BusyBox 1.36.1を動かした。通信は127.0.0.1だけ。フラッシュは模擬ファイルだけで、`/dev/mtd*` は開いていない。

7検査すべて成功（合計約4分）。

| 検査 | 結果 | 秒 | 内容 |
| --- | --- | --- | --- |
| `runtime_patch` | PASS | — | OpenIPCのmusl BusyBoxと `ld-musl` を、interpreterのパスだけ書き換えて `/dev/om` から動かせる |
| `runtime_on_stock_root` | PASS | — | 純正rootfsの上で、書き換えたBusyBoxが必要なアプレットをすべて実行できる |
| `shells_parse` | PASS | — | `stage2.sh` を純正BusyBox 1.18.4のshで、`om_stockboot.sh`・`migrate_hook.sh`・`trial.sh`・`trial_dhcp.sh` をそれぞれ実行するshで構文検査 |
| `stock_applets` | PASS | — | 純正BusyBoxのアプレット一覧がE2Eの許可リストと一致すること。純正 `dd` が `conv=` などを拒み（終了コード1）`if/of/bs/count/skip/seek` は通すこと、`grep` に `-x` がなく行頭と行末の固定は効くこと、純正shの `1<>` でtlの雛形にログを重ねても残り（`OMTLOG-END` と0埋め）が壊れないこと、`wget --post-data` が本文を%XX形式（`application/x-www-form-urlencoded`）で送り、それが `tests/om_testlib.py` の模型と一致し、`serve_migration.py` が元のログに戻せること、E2Eで模擬・禁止にしている `udhcpc`・`mount` と `wget` に `trial.sh` が渡す引数を解釈できること（空のユーザー名前空間の中で） |
| `etc_overlay_mount` | PASS | — | 試験起動で `/etc` の上に設定を重ねる方法（bind mount）が純正のshとBusyBoxで動く |
| `runapp_trial_branch` | PASS | 17 | 移行appfsの `runapp.sh` を純正shで実行：通常起動、試験起動、ペイロードなしの即再起動、追加行の範囲 |
| `e2e_real_binaries` | PASS | 227 | E2Eの12件を、純正BusyBox（phase1）とOpenIPC BusyBox（phase2、installer）の実物で通す |

### 検証で見つけて直した不具合

1. **env終端の後ろの残骸**：純正U-Boot 2010.06の `_do_setenv` は、envを詰め直しても終端より後ろを消さない。試験起動の後のenvには、終端の後ろに `omtrial=1` 入りの古い `bootcmd` が残る。`om_stockboot.sh` はこれも読んでしまい、純正に戻ったenvを「試験用のまま」と誤判定していた。終端（最初の空行）で読むのをやめるよう直した。模擬U-Bootを `_do_setenv` と1バイト単位で同じ動作にし、回帰テストを足した。
2. **純正BusyBoxで動かない書き方（3件）**：(a) 純正BusyBox 1.18.4の `dd` は `if=`・`of=`・`bs=`・`count=`・`skip=`・`seek=` しか受け付けず、`conv=`・`ibs=`・`obs=`・`iflag=`・`oflag=` を渡すと何もせずに終了コード1で終わる。`trial.sh` は `conv=sync`／`conv=notrunc` を使っていたので、実機の試験起動では `tl` へのログが一度も書かれず、`saveenv` が失敗していた場合の純正envの書き戻しも失敗するところだった（書き戻せなければ試験起動を繰り返し、SPIでの復旧が要る）。ホストが作る64 KiBの雛形（`tl-blank.img`：0埋めと `OMTLOG-END`）にログを `1<>` で重ね書きし、`tl` と `senv` は区画全体を `conv=` なしで書くよう直した。(b) 純正の `grep` には `-x` がない。`trial.sh` はmanifestの照合に `grep -qx` を使っていたので、ホストに届いても必ず `host=fail` と報告し、finalへ進めなくなるところだった。行頭と行末を固定した `grep -q` に直した。(c) 純正の `wget --post-data` は、本文の英数字と `!&'()*-.=_~` 以外をすべて%XXにして送る（`application/x-www-form-urlencoded`）。`serve_migration.py` は試験ログを届いたまま保存していたので、`build_stockboot.py collect` がフラグを1つも読めず、試験起動が成功していてもfinalへ進めなくなるところだった。`serve_migration.py` がこの形式を見分けて元に戻すよう直した（ログには必ず空白と改行があり、%XX形式の本文には現れないので取り違えない）。3件ともx86の試験はDebian版BusyBoxで通っていて、qemu上の実物で通したE2Eで分かった。純正shで動くスクリプトはビルド時に (a)(b) を検査し、x86のE2Eの `dd` と `grep` は純正と同じものしか通さず、`wget` は純正と同じ形式で送るようにした。qemuの検査には、純正の `dd`・`grep`・`wget --post-data` の挙動、`1<>` での重ね書き、E2Eで模擬・禁止にしている `udhcpc`・`mount` と `wget` の引数解釈（空のユーザー名前空間の中で）を足し、E2Eには書き戻しの経路と、ホストが受け取った試験ログが `trial.sh` の書いたものと一致することの確認を足した。
3. **受信を断るときの応答**：`serve_migration.py` は大きすぎる受信などを断るとき、本文を読まずに接続を閉じていた。送り手には応答の代わりに接続のリセットが届くことがあり、x86の試験がときどき失敗した。1 MiBまでは本文を読み捨ててから応答するよう直した。
4. **検証側の誤り3件**：runapp.shの試験分岐の照合文字列が1文字ずれていた。純正BusyBoxに `readlink` がないことを見落としていた。qemu上で遅い純正shに、ホスト向けの時間しきい値（1.5秒）を当てていて、負荷が高いと失敗した。時間で判定するのをやめ、起動行が待機に入ったことをファイルで確かめてから先へ進めるようにした。どれも製品側のコードではなく、検証側を直した。
5. **検証の後始末**：検証をSIGTERMで止めると、テスト・模擬サーバ・`qemu-arm` の子プロセスと一時ディレクトリが残っていた。子を専用のセッションで動かし、止めるときにまとめて終了させるようにした。
6. **足した検査（2件）**：(a) 弱いブロック（`0x6c4f28` を含む64 KiB）を16回読んだ結果が1回でも違えば、`build_stockboot.py diag` は先へ進まない。(b) OpenIPCのkernel（`hisfc350`）は起動時に `0x050000` だけを見て、uImageヘッダがあれば独自の区画表を登録し、`mtdparts=` と `root=` を無視する（kernelのパッチと文字列から確認。実機では未確認）。この方式では `0x050000` は純正envの中で、文字と0埋めしかない。試験用envと移行後envを作るときに、ここにuImageのmagicがないことを検査するようにした。
7. 以前の方式で見つけて直した2件（OpenIPCの `wget --post-file` が最初のNULで止まるのでmdatの退避をbase64で送る、純正shが `$((...))` なしでビルドされている）は、この方式にも引き継いでいる。

## 未検証のことと実機の前に必要なこと

- `0x96` の送信はリポジトリに入れていない（実機ではリポジトリ外の私的な送信ツールを使った）。appfsとbakfsを両方書き換えて再起動する。
- 純正U-Bootが、こちらのenvを実際に実行して `saveenv` できるか（probe）。純正U-Bootのコマンド解釈はバイナリから読み取ってモデル化しただけで、実機では動かしていない。
- OpenIPC kernelがこの基板で起動するか。SPIフラッシュ素子の認識、純正rootfs（xz）のマウント、`mtdparts` の解釈が分かるのは試験起動のときだけ。kernelがユーザーランドに着く前に止まった場合は、900秒のタイマーが動かないので、手で電源を入れ直す（純正に戻る）。（2台目で起動を確認。下の「2台目の実機移行」）
- MT7601を、OpenIPCのモジュールと純正ユーザーランドで接続できるか（qemuでは模擬）。移行後のOpenIPCはwpa_supplicantを使うので、その経路は試験起動では確かめられない。移行後にWi-Fiでつながらなければ、戻る手段はSPIだけになる。（2台目では移行後もWi-Fiで接続できた）
- 既存bakfsの読出し不一致は未解決（[調査](../../docs/BAKFS-READBACK-INVESTIGATION.md)）。
- AR0141センサーは、2台目のOpenIPCで映像を確認した（[video/README.md](video/README.md)）。
- 実機の空きRAM、`/dev` のtmpfs容量、watchdogデバイスの有無は、diagnoseで初めて分かる。ただしwatchdogは純正ユーザー空間（`cmsMain.out`）が `/dev/mem` 経由で給餌しており、デバイスの有無では判定できない（finalの結果）。
- 試験起動で `tl` と `senv` へ書く経路（`mtdblock7`／`mtdblock8` を開く `dd`）は、純正 `dd` と `1<>` の動作をqemuで確かめ、模擬ファイルに通しただけ。実機のMTDでの書込みは試験起動で初めて分かる。
- Wi-Fiの設定は、試験用SquashFS（xz圧縮、bakfs領域）と、移行後の `0x060000` のenv（OpenIPCの通常どおり平文）に入る。試験で止めた場合、bakfs領域に残る。

## RAMFS容量判定の追加検証

初期の容量判定検証（150件・qemu-arm 8項目）は、RAMFSの `stat -f` 出力をfixtureで `ramfs` としており、実物のBusyBox 1.18.4が本物のRAMFSを `romfs` と表示する問題を検出できなかった。従来の記録は [`results/ramfs-capacity-validation.json`](results/ramfs-capacity-validation.json) に限界を明記して残した。

追加修正では、表示名だけでなく最長一致する一意な `rw` のRAMFS/tmpfsマウントを確認する。非特権のuser/mount namespaceで本物のRAMFSを作り、旧判定は失敗、修正版は成功する回帰テストを追加した。実物のARM版 `stat` / `df` / `awk` を使い、読み取り専用RAMFSと実際に満杯にした4 KiB tmpfsは拒否することも確認した（RAM値だけは再現性のためfixture）。

追加修正後は、単体・結合テスト162件、隣接するopenipc-portの25件、qemu-armの9項目がすべてPASSした。純正BusyBox実物で容量判定23件とオンライン経路16件がPASS。イメージは2,613,248 / 2,621,432 bytes（余り8,184 bytes）で、埋め込まれたstage2もソースと一致する。記録は [`results/ramfs-stat-alias-validation.json`](results/ramfs-stat-alias-validation.json)。これは追加修正のオフライン結果であり、実機diagnoseの成功や追加送信・試験起動・finalの実行許可を意味しない。

## 読取り専用診断の継続性

diagnoseでは純正アプリ・ネットワークサービスを停止しない。サービス停止によってWi-Fiやwatchdogへの給餌を妨げないための変更であり、以前の実機中断がwatchdog原因だったと断定するものではない。書込みactionのquiesceと確認語句は変更していない。純正アプリの稼働中にデータが変われば、従来どおり反復読取りの不一致として先へ進まない。

基本情報（uptimeを含む）を最初に送信・確認してから全ブロックのハッシュを計算し、3巡それぞれの区画開始を通知する。後段で中断しても、先に届いた基本情報と進捗を原因調査に使える。standbyでも `--backup-dir` を指定すれば受信イベントを私的なログへ保存するが、GO・ファイル配布・アップロード受入れ・書込み・再起動の許可は与えない。更新送信後はstandbyで起動が落ち着くのを待ってから、読取り専用診断を開始する。

オフライン検証は単体・結合170件（新規回帰8件）、隣接25件、qemu-arm 9項目がPASS。純正ARMバイナリのオンライン経路16件・容量判定23件もPASSし、appfsは2,613,248 / 2,621,432 bytes（余り8,184 bytes）。記録は [`results/diagnostic-liveness-validation.json`](results/diagnostic-liveness-validation.json)。これは実機での診断・saveenv・OpenIPC試験起動の成功を意味しない。

## watchdog対策の検証（オフライン）

カメラにもフラッシュにも触れていない。結果は [`results/wdtgate-offline-qa.json`](results/wdtgate-offline-qa.json) と [`results/online-qemu-validation.json`](results/online-qemu-validation.json)。

- `tests/test_migrate_hook_wdt.py`（10件）：模擬SP805（ロック、WdogControl、カウンター）に対して `migrate_hook.sh` を動かす。停止と再始動、再起動前の再始動でプロセスを止めたままにすること、二重の停止で保存値を上書きしないこと、もともと止まっていたwatchdogを動かさないこと、止まらない・読めないときに停止を取り消すこと、`devmem` の不正な出力や壊れた保存値を書かないこと、watchdogデバイスがあれば何も触らないこと。
- `tests/test_build_stockboot.py`：diagnoseがwatchdogのレジスターと `diag-uboot` を記録すること、wdtgateの合否、wdtgateなしではfinalを作らないこと。
- `tests/test_e2e_online.py`：模擬フラッシュと模擬SP805で、wdtgateを含む全段階をネットワーク越しに通す。watchdogが止まらない、または読めない場合は純正のまま止まる。
- 単体・結合テスト220件（失敗・スキップなし）、隣接するopenipc-portの25件が成功。
- qemu-arm（9項目すべてPASS）：実バイナリのE2E 18件（watchdog関連の3件を含む）、容量判定23件。純正rootfs上のパッチ済みOpenIPC BusyBox（227アプレット）で、`devmem` が返す形の16進数をシェル算術で扱えること（`sh_arith_devmem_words`）も確かめた。
- SP805を実際に止められるか、止めた状態でWi-Fiが保てるかは、実機のwdtgateで初めて分かる。

## 2台目の実機移行（ネットワーク経由、2026-11-16）

結果は [results/unit2-network-migration.json](results/unit2-network-migration.json)。

- 純正ファームのWi-Fi APモードから、UART・Ethernet・SPIクリップなしで移行した。U-Bootは書いていない。
- diagnose → wdtgate（SP805を304秒止めて再開）→ probe → 試験起動（16/16）→ final の順に通った。finalはPONR後の書込みをすべて読み戻して確認し、再起動した。
- 移行後のOpenIPC（kernel 3.0.8）はホストのAPにつながり、DHCP・ping・SSH(22)・HTTP(80)が応答した。HTTPは初回セットアップ画面。rootfs_dataのjffs2はリセットをまたいで残る（初回起動で作られたSSHホスト鍵が変わらない）。
- 移行直後の問題: 約40秒ごとに再起動した（1回の起動でSSHは約14秒、HTTPは約6秒しか開かない）。SP805が起動時から動いたままで、だれも給餌していないと判断した。OpenIPCは `wdt.ko` を `load_hisilicon`（S70vendor）の `insert_ko` の最後でしか読み込まない。この基板では、検出されてenvに保存されたセンサーIDが `insert_sns` の一覧になく、`report_error` で抜けるので、そこまで届かない。試験起動は自分で `wdt.ko` を読み込んでいたので生き残った。所有者のセットアップ後に `wdt` を読み込むと止まったので、この判断は実機でも確かめられた（下）。
- 初回セットアップ（Majestic EULAへの同意とrootパスワード）は、カメラ自身が「所有者本人が入力すること、AIや自動化は代行しないこと」と求めているので、同意は所有者がSSHの対話で自分で入力した。質問が出てからリセットまで約6秒しかないので、所有者が先に決めたパスワードを、同意のあとに出る `passwd` の2つのプロンプトにだけ、リポジトリ外の補助スクリプトが入れた（同意の語は入れない）。
- 続けて `/etc/modules` に `wdt` を足して読み込み、1回再起動して123秒動き続けることを確かめた。以後DHCPの取り直しは止まり（確認時点で約6分）、ping・SSH(22)・HTTP(80)が応答する。SSHはパスワードを求める。
- 接続に時間がかかる理由: TCP接続からdropbearのバナーまで約5.1秒かかる（起動処理の負荷）。鍵交換は約1.5秒で、既定のcurve25519+ed25519が最速だった（ECDH P-256は2.0〜2.2秒）。
- 映像（続き）: envの `sensor` を `ar0141` にし、AR0141のini、`isp.blkCnt: 9`、起動直後と動作中の映像を見張る `S96vicheck` を入れて、720pのH.264が約27 fpsでRTSP(554)から出るようになった（[video/README.md](video/README.md)、[results/unit2-video.json](results/unit2-video.json)）。
- 暗視: IRカットフィルター（GPIO0_2）、赤外LED（GPIO6_4）、明るさセンサー（ADCのチャネル1）を見つけ、majesticの `nightMode` と、センサーをmajesticに読ませる `S94lightsensor` で、昼と夜を自動で切り替えるようにした。Web画面とAPIからも切り替えられる（[night/README.md](night/README.md)、[results/unit2-night.json](results/unit2-night.json)）。
- Wi-Fi: つなぎ先を、ホストのAPから自宅のルーター（Aterm）の2.4 GHzに変えた。フラッシュに書かない試験（残す印がなければ300秒で戻る）でつながることを確かめてから、envの `wlanssid` と `wlanpass` を `fw_setenv` で書き換えた。再起動後も同じところにつながり、時計はNTPで合うようになった（[wifi/README.md](wifi/README.md)、[results/unit2-home-wifi.json](results/unit2-home-wifi.json)）。
- 次の機体では、`make_env.py` が既定でenvに `sensor=ar0141` を入れるので、初回起動から `load_hisilicon` が `wdt.ko` まで読み込み、watchdogに給餌できる見込み（次の機体で確かめる）。finalで rootfs_data の先頭に `/init` の持ち込み（`OPENIPC-OVL1`）を置いて `etc/modules` に `wdt` を入れる案は未実装。
- 更新: OpenIPC標準のオンライン更新は固定の区画を壊すので、更新ガード（`/usr/sbin/sysupgrade` の置き換え）とenvの `upgrade`（届かないURL）で止めた。ガードの判定は2台目の上で28件確かめた（書き込みなし）。区画に収まる版だけを `--kernel`／`--rootfs` で入れる手順は [update/README.md](update/README.md)（版の入れ替えは未実施）。結果は [results/unit2-update-guard.json](results/unit2-update-guard.json)。
- まとめ: ここまでの手作業を [easy/README.md](easy/README.md) の2つのコマンドにした。`post_setup.py`（移行後の設定と更新ガード）は2台目で確かめた。`easy_migrate.py`（移行全体）は模擬カメラ相手のオフライン試験だけ。

## 再実行

`tests/` と `validate_online_qemu.py` はカメラにもフラッシュにも触れない。

```sh
export PYTHONDONTWRITEBYTECODE=1
sha256sum -c research/openipc-migrate/SHA256SUMS   # リポジトリのルートで
cd research/openipc-migrate
# mksquashfs/unsquashfsがあれば、ビルダーと試験用SquashFSの実ツール試験も動く（無ければスキップ）
OM_MKSQUASHFS="$HOST_MKSQUASHFS" OM_UNSQUASHFS="$HOST_UNSQUASHFS" python3 -m unittest discover -s tests -v
python3 stock_layout.py plan --firmware "$PUBLIC_ARTIFACTS/firmware.bin.hi3518ev100"

# 私的な入出力はリポジトリの外。wifi.varsは0600のkey=valueファイル（wlanssid、wlanpass）
python3 make_env.py --uboot "$PUBLIC_ARTIFACTS/u-boot-hi3518ev100-nor.bin" \
  --private-vars "$PRIVATE/wifi.vars" --layout stockboot --output-dir "$PRIVATE/env"
python3 build_migration_appfs.py --firmware "$PUBLIC_ARTIFACTS/firmware.bin.hi3518ev100" \
  --oenv-image "$PRIVATE/env/env-openipc.img" --stock-appfs "$PRIVATE_INPUTS/stock-appfs.part" \
  --host 192.0.2.10 --mksquashfs "$HOST_MKSQUASHFS" --unsquashfs "$HOST_UNSQUASHFS" --out "$PRIVATE/bundle"
python3 build_stockboot.py plan --firmware "$PUBLIC_ARTIFACTS/firmware.bin.hi3518ev100" \
  --oipc-rootfs "$OIPC_ROOTFS" --auth-runtime "$AUTH_RUNTIME" --mksquashfs "$HOST_MKSQUASHFS" --bundle "$PRIVATE/bundle"

# qemu-armが動的リンクなら、必要なLD_LIBRARY_PATHを --qemu-lib-path で渡す
python3 validate_online_qemu.py --bundle "$PRIVATE/bundle" --stock-rootfs "$PRIVATE_INPUTS/stock-rootfs" \
  --qemu-arm "$QEMU_ARM" --qemu-lib-path "$QEMU_LIBS" --unsquashfs "$HOST_UNSQUASHFS" \
  --mksquashfs "$HOST_MKSQUASHFS" --out-json "$out/online-qemu-validation.json"
```

`$OIPC_ROOTFS` は公開firmwareのrootfsを展開した木。`192.0.2.10` は説明用のアドレスで、実際のLANアドレスはGitに書かない。`serve_migration.py` は実際にカメラの相手をするサーバなので、上の再実行には含めていない。

## Gitに入れないもの

bundle、移行appfs、env画像、試験イメージと試験用SquashFS、action一式、`build_stockboot.py` の状態ディレクトリ、カメラからの受信物（diagnose、collect、mdat、ログ、静止画・映像）、firmware・U-Bootの実体、純正appfs・rootfs、SPIダンプ、run id、LANアドレス、SSID・パスフレーズ、私的ログ、更新envelope。`.gitignore` だけに頼らず、追加するファイルを明示的に選んで中身を確かめる。
