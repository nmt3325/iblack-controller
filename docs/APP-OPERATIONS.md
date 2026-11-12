# iermu Androidアプリの操作・通信経路調査

対象：`com.intl.iermu` **1.8.0_1224 (577)**。既存APKのJADX展開結果を対象に、画面 → Business → HTTP / LAN SDK → CMS / native の順で調査した。実施日は2026年11月12日（日本時間）。

## 結論と読み方

アプリ内の操作は、**カメラへ直接送るLAN制御、メーカーのクラウドAPI、スマホ内の再生・保存処理**に分かれる。同じ画面に並ぶ機能でも経路は同じではない。DNSでメーカー名をLinuxへ向けるだけでは、クラウドAPI依存の画面操作は復活しない。

この資料は**静的な操作経路の棚卸し**であり、「列挙した全機能をこのカメラで実行できた」という意味ではない。今回、新しい設定書込み、再起動、録画変更、SD初期化、NASへのアクセス、クラウドアカウント操作、スピーカー送信は実行していない。3356と通常RTMPの並行試験も行っていない。

- **既確認**：これまでの実機試験で確認した機能。今回の再実行とは区別する。
- **実装確認**：APKで画面・Business・通信呼出しを確認した機能。サーバー応答・本体対応・実動作は別問題。
- **SDKのみ／未確定**：APIやnative入口は存在するが、通常画面からの到達性・機種対応・内部処理が未確定。
- **端末処理**：スマホの表示・音量・保存・共有など。本体の設定変更ではない。

## 保存した資料

| ファイル | 内容 |
| --- | --- |
| `docs/APP-OPERATIONS.md` | 本文：操作群、値、経路、条件、保存と危険性 |
| `docs/APP-API-INDEX.md` | 全契約宣言と、同名メソッドを参照するUI・実装の索引 |
| `docs/APP-UI-INDEX.md` | UIファイル別のクリックID・操作参照の索引 |
| `evidence/app-operations-index.json` | 機械可読の宣言・参照・CMS代入順・入力ハッシュ |
| `evidence/app-operations-details.json` | RPC呼出し、設定JSONキー、手動で確認した値・副作用 |
| `evidence/app-operations-summary.json` | 対象、範囲、限界、作業前後の実再生結果 |
| `research/index_app_operations.py` | 既存JADXツリーを読むだけの再現ツール |
| `research/build_app_operations_report.py` | 索引から付録・根拠を生成するツール |
| `tests/test_app_operations.py` | 合成Javaによるオフライン検査10件 |

索引の宣言数は、HTTP 128、Business 379、LAN 28、SDK公開入口24、nativeラッパー入口24（非公開の初期化3件を含む）。**層をまたぐ重複・オーバーロード・キャッシュ・通知補助を含むため、583種類のユーザー機能という数え方はしない。**

## 1. ライブ映像・音声・端末内の保存

| 操作 | アプリ側の入口・実行条件 | 処理経路・注意 | 今回の個体 |
| --- | --- | --- | --- |
| 自分のカメラのライブ開始・終了 | `IStreamMediaBusiness.openLiveMedia/closeLiveMedia`、MineLive | 接続情報取得とLAN/クラウドのメディア経路を組み合わせる。CMS 3356とRTMPは別の取得経路 | 映像・マイク受信は既確認。通常中継はRTMP pullのまま |
| 公開カメラ／パスワード付き公開映像 | `openPubLiveMedia/openPubLiveMediaWithPassword`、PublicLive | 公開・認可・クラウド側情報に依存 | この個体の公開設定は未変更 |
| 視聴画質の低・HD・Super切替 | MineLive `onLevelClick`、`isDoNekWork`がtrueの場合だけ送信 | `setBitLevel(deviceId,0/1/2)` → クラウドAPI。画面アイコンだけの更新時は送らない | 画面からの設定変更は未実行 |
| 再生音のON/OFF | MineLive `onSoundStateChange`、録画画面にも切替 | プレイヤー側の消音。本体マイク設定の`setDevAudio`とは別 | 受信音声は既確認 |
| カメラに話しかける | MineLive長押し、ネットワークと録音権限が必要 | 押下後500ms遅延して開始、離すと停止。Baidu/Linyangで送信入口が分岐 | スピーカー実音は未確認 |
| 静止画キャプチャー | `imageButtonPrintScreen`／`onPrintScreenClick` | 表示画像をスマホへ保存。本体SDの写真撮影コマンドとは限らない | 本体新規操作なし |
| ライブ映像の端末内録画 | `screen_cap_btn` → `setScreenCapStart`、停止は`videoView.stopRecord` | スマホ側録画。短時間録画を拒否するUI条件あり。本体の連続録画設定とは別 | 未実行 |
| 全画面・縦横表示 | MineLiveの画面回転・コントローラー | スマホ側表示 | 本体機能とは独立 |
| 拡大・ジェスチャー | ライブ画面とコントローラーのtouch処理 | 表示操作とPTZ操作を区別する。PTZモードでは物理移動へつながる | 物理移動は未試験 |
| 4画面表示・表示カメラ選択 | FourLiveMain等 | スマホ側で複数再生。カメラが4本のストリームを生成する意味ではない | 同時接続上限は未試験 |
| ライブから録画画面への遷移 | MineLive `live_jump_rec_rl` | クラウド録画とSD/NAS録画の入口へ分岐 | 未実行 |

**送話の条件**：`MineLiveFragment:1075–1128`の押下・解放、`285–300`の接続方式分岐、`VideoView`のmedia player有無を確認。`connectType=2`ならLinyangの送話、その他はBaiduトークン付き経路。これはLAN/WANの番号ではない。音声形式の既知の解析結果はAAC-LC / 11025 Hz / mono。送信成功と実スピーカー出力は未達。

## 2. カメラ設定・電源・ファームウェア

以下の設定は、今回のAPKでは主に`CamSettingBusiness → CamSettingApi → CamSettingService`を通る。UIにあるからといって直接CMSへ送るわけではない。

| 操作 | 値・条件 | アプリの通信 | 状態 |
| --- | --- | --- | --- |
| 状態LED | スイッチのON/OFF | `updatesetting`、JSON `light=1/0` | 実装確認 |
| 映像上下反転 | スイッチのON/OFF | JSON `invert=1/0` | 実装確認 |
| 本体マイクの有効／無効 | スイッチのON/OFF | JSON `audio=1/0` | 再生側の消音と別。書込み未試験 |
| 屋内／屋外 | Indoor/Outdoor | JSON `scene=0/1` | 実装確認 |
| 夜間・IRモード | **0=自動、1=夜間無効、2=夜間有効** | JSON `nightmode` | 独自の直接制御ではカラー／モノクロ切替を既確認。クラウド経路の再現は別 |
| 露出モード | **0=Auto、1=Highlight priority、2=Lowlight priority** | JSON `exposemode` | 明部優先／暗部優先。フレームレートや50/60Hz設定と推測しない |
| ライブ画質 | Low/HD/Super = 0/1/2 | JSON `bitlevel=0/1/2` | SD/NASの`setCodeGrade`と別 |
| 上り速度上限 | 速度入力・保存 | JSON `maxspeed`と`minspeed="50"` | 単位・個体の反映は別途確認 |
| カメラ名 | 入力完了 | `method=update`、フォーム`desc` | クラウド上の名前変更 |
| タイムゾーン | 選択完了 | `method=update`、フォーム`timezone` | 本体への時刻反映は未試験 |
| 電源ON/OFF | 電源スイッチ、接続方式による追加処理あり | JSON `power=1/0`。OFF時は`power_cron=0`も設定 | 撮影・配信停止を伴い得る。未実行 |
| 電源の曜日・時間指定 | 電源スケジュール画面。オフライン時は入口を拒否する分岐あり | `power_cron/start/end/repeat` | 未実行 |
| カメラ再起動 | 再起動画面・確認 | JSON `restart=1` | 配信断を伴うため未実行 |
| クラウド登録解除 | 登録解除の確認 | `method=drop` | **工場出荷初期化と同じとは確認していない**。未実行 |
| 情報・更新確認 | カメラ情報、バージョン、更新状況 | `setting` / `checkupgrade` / `upgradestatus` | サーバー依存 |
| 通常のファームウェア更新 | 更新画面 | `method=upgrade` | 書換え・再起動を伴い得る。未実行 |
| NAS対応カーネル更新 | LocalRecord/NAS設定の更新処理 | LAN `75/42`、5-byte bodyにHTTP port 80とtype=2 | `kernelUpgrade`。通常更新とは別。未実行 |

設定画面は状態取得完了までLED・反転・マイクのスイッチを隠し、成功後に表示する。Business層には、LED等のキャッシュを先に変更して失敗時に戻す処理もある。**画面が切り替わっただけでは実機反映の証拠にならない。**

### HTTPの実際の形

- 多数の操作は`/v2/pcs/device`へのフォーム送信。
- `method`、`deviceid`、`access_token`と、設定JSONを入れる**`fileds`**を使う。綴りは本当に`fileds`であり、`fields`に直してはいけない。
- JSONキーの例：`light`、`invert`、`audio`、`scene`、`nightmode`、`exposemode`、`restart`、`power`。
- 読取りは`method=setting`、`type`に情報・状態・電源・録画・警報・画質の種別を指定する。文字列は実際の呼出し記録を参照する。
- SDKのベースURL変更入口はあるが、通常ユーザーの設定画面で任意クラウドURLを編集できる証拠とはしない。

**Linux側でこれらを使えるようにするには**、DNS誘導に加え、認証・各RPCの応答・状態データ・必要なら本体へ命令を配送する部分まで実装する必要がある。現在の最小クラウド応答は、その全体を実装していない。

## 3. SDカード・NAS・録画／再生

| 操作 | 画面・条件 | 通信／データ | 注意 |
| --- | --- | --- | --- |
| SDカード情報 | SD/LocalRecord | LAN `74/1` | 応答は機種依存。今回未取得 |
| NAS設定読取り | NAS/LocalRecord | LAN `74/62` | 認証情報を含み得るのでraw値を公開しない |
| NAS ON/OFF、SMB/NFS選択、サーバー・共有・容量指定 | NAS設定保存 | LAN `75/62` → 状態読返し | `setDevParam`の自動save例外。明示saveなし |
| SMB共有・フォルダー検索 | ScanNasDir | **スマホからSMBへ直接アクセス** | 読取りだけではない。必要時に`iermutest`ファイルを作成して削除し、書込み権限を調べる |
| NFS共有パス取得 | NAS設定 | `74/68`と`74/69` | 一つのメソッド内で複数要求 |
| 録画方式 | SD/LocalRecord | `74/43`、書込み`75/43` | `0=continuous`、`1=change`のUI。アプリはbody[5]bit7を使う |
| 録画画質 | SD/NASの画質ダイアログ | `74/76`と`74/5`を取得、`75/5` | 機種種別で選択値を変換。今回の既読種別では200/500/1000。ライブ画質と別 |
| SD/NAS録画一覧・日付一覧 | MineCardRecord等 | `66/21`、`77/1`等 | ファイル・日付・期間に依存 |
| SD/NAS録画再生・停止 | 録画画面 | `75/64`などのセッション操作 | 名前がgetでも再生状態を変えるAPIがある |
| 再生位置・seek | 録画タイムライン | `74/64`、再生設定`75/64` | 視聴・セッション状態を変え得る |
| 再生一時停止、音声、日付、タイムライン／サムネイル | MineRecord/MineCardRecord | プレイヤー操作＋録画メタデータ | 表示だけの操作とネットワーク要求を分ける |
| 録画の区間切出し・端末保存 | `ICamRecClipBusiness`等 | Baidu/Linyang/localで別実装 | native RTMP→MP4入口も存在。サーバー・保存権限に依存 |
| ダウンロード状態・再試行 | 録画／クリップ画面 | native downloader＋端末管理 | カメラ設定変更と別 |
| **SD初期化** | SD画面の確認後 | **`75/83`**、進捗取得`74/83` | **録画を失う破壊的操作。今回は実行していない** |
| クラウド録画ON/OFF・時間・曜日 | CloudRec/スケジュール | `cvr`、`cvr_cron/start/end/repeat` | クラウドAPI依存 |
| クラウド録画一覧・契約容量・プラン案内 | CloudRecInfo/CloudRecService | 契約・保存API、Web画面 | サービス／課金状態依存。操作・購入なし |

**NAS設定bodyの静的配置**：142 bytes。offset 0はONフラグ、SMBはbit7、NFS経路はbit1。ユーザー名は1、パスワードは34、パスは67、デバイス用フォルダーは118、IPv4の4 byteは134–137、容量は138–141（ネットワーク順）。これは実装上の配置の記録であり、現物の認証値は採取・記載していない。

**録画方式の書込みに注意**：アプリは6-byteのゼロ初期化bodyを作り、byte 5のbit7だけを設定する。既存構造を読んで該当bitだけ変更するread-modify-writeではない。別機種にそのまま適用すべきではない。

## 4. PTZ・巡回・プリセット

| 操作 | LAN側の命令 | LAN外／クラウド側 | 条件・到達性 |
| --- | --- | --- | --- |
| ドラッグ座標による移動 | `75/66`、x/y始終点をBE32×4 | `cloudMove`、x1/y1/x2/y2 | MineLive → `ICloudPaltformBusiness.cloudMove`。`camIsInLan`で分岐 |
| 方向・歩幅の移動 | 別のクラウド入口 | direction/step、初回delay | `outSideCloudMove/First` |
| 自動巡回開始 | `71/11`、body `01 04 01` | rotate=`auto` | MineLiveに呼出しあり |
| 自動巡回停止 | `71/12`、body `01 04 01` | rotate=`stop` | MineLiveに呼出しあり |
| プリセットへ移動 | `71/19`、body `01 <number> 01` | `cloudMovePreset` | LAN判定で分岐 |
| プリセット追加 | SDKに`71/18`あり | 通常Businessは`addPreset` API | **SDKにある命令と、画面から使う経路を混同しない** |
| プリセット名変更・削除・一覧 | カメラ直接APIとは別 | `updatePresetTitle/dropPreset/getListPreset` | MineLiveの編集・削除操作から到達 |
| 巡回中か／XY移動対応の取得 | `74/66`を用いる実装 | `getPlatInfo`、配信情報等 | 取得成功だけで物理モーターの存在は確定しない |
| 雲台対応問合せ | SDKに`71/26` | 配信情報のplatform有無も利用 | 動作系と同じmain71。今回は未送信 |

LANの場合でも、エラー種別によってクラウドへ進む／終了する分岐がある。プリセット追加・名前等は、LAN命令の存在だけではクラウド非依存にならない。**この個体のPTZモーター・可動機構への対応は未確認。**

## 5. 警報・通知・433アクセサリー

| 操作群 | アプリの入口 | 経路・設定 | 条件・注意 |
| --- | --- | --- | --- |
| 動体検知ON/OFF・感度 | AlarmItemSetting/AlarmLevel | JSON `alarm_move`、`alarm_move_level` | クラウド設定。個体実効値は未試験 |
| 音検知ON/OFF・感度 | 同上 | JSON `alarm_audio`、`alarm_audio_level` | マイク有効化・再生音量・送話とは別 |
| 警報プッシュON/OFF | AlarmItem/AlarmList | `alarm_push` | スマホ側の通知許可とも別 |
| 警報曜日・時間 | CronSetting | `alarm_cron/start/end/repeat` | 保存時にクラウドへ送る |
| アラーム一覧・画像・履歴・既読 | `ICamAlarmBusiness` | アラームAPI、ページング | クラウド保存情報 |
| 警報履歴削除 | `delAlarmData` | 削除API | 破壊的。未実行 |
| 通知受信・音・振動・鳴動間隔 | AlarmMsg/SoundFreq | 端末設定とプッシュ登録 | UIに1/5/10/30分の選択肢。カメラの送信間隔と同一視しない |
| WeChat通知・プッシュ登録 | 通知設定 | アカウント状態、Baidu/Getui登録 | 外部サービス依存 |
| 警報連絡先の追加・編集・削除 | ContactPhoneList/EditContact | クラウド連絡先API | 実際の連絡先を読取・変更していない |
| 433センサー登録・QR・命名確認 | Dev433Detail等 | `add433alarm/check433Name` | 対応受信ハードウェアが必要 |
| 433センサー名・有効状態・削除 | Dev433Setting/EditName | `update433DevName/Status/delete433Dev` | この個体の対応未確認、未実行 |
| 433警報クリア・動作テスト・電池低下通知 | Dev433Test系 | `clear433Alarm`、通知・端末状態 | 実警報や外部への通知は発生させていない |
| 環境センサー／AirCapsule値 | `AirCapsuleApi.getAirCapsuleData` | SDK `74/74` | SDK実装あり。搭載・UI利用・実値は未確認 |
| システム通知・既読 | `ICamSystemNoticeBusiness` | 通知API＋端末キャッシュ | カメラ制御ではない |

## 6. セットアップ・Wi-Fi・アカウント・共有

| 操作群 | アプリ入口／経路 | 条件・範囲 |
| --- | --- | --- |
| 近くのカメラ／Wi-Fiスキャン、停止 | `ISetupDevBusiness`、SetupDev SDK | Androidの無線・権限。スキャン開始／停止・指定機体スキャンもある |
| SSID/BSSID選択、AP方式の手動誘導 | `scanConnectSSID/BSSID/notifyManualAP` | スマホの無線接続とカメラ設定を区別する |
| SmartConfigによる接続情報送信 | native `send_smartconfig_wifi` | 内部はnative。実際のWi-Fi認証値を保存資料に含めない |
| カメラへのWi-Fi・ネットワーク設定 | SetupDev / CMSの既存解析 | 設定により通信断・別AP接続が起きる。今回変更なし |
| IP/DNSの詳細設定 | advanced設定とCMSの既存解析 | 現行クライアントはDNS書込み・保存・再接続を既確認。通常の全アプリ画面で同じ項目が見えるとまでは断定しない |
| メーカーへのデバイス登録・再登録 | `registerCamStep` → `registerDev` | `device_type/connect_type/desc`等とアカウントトークン |
| 自分／認可されたカメラ一覧、検索、カテゴリ | `IMimeCamBusiness`、DeviceSearch/Category | クラウド一覧と端末キャッシュ |
| 個別共有リンク・公開共有リンク・共有解除 | `ICamShareBusiness` | サーバー上の共有状態を変更。今回は未実行 |
| 認可コード／ユーザーへの権限付与・解除 | `grantShare/dropGrantUser/dropGrantDevice`等 | アカウント・権限依存 |
| 設置位置検索・登録・非表示・削除 | CamLocation系 | 位置サービス＋クラウド。実位置は取得していない |
| 公開カメラ検索・視聴・お気に入り・評価・コメント | `IPubCamBusiness/IPubCamCommentBusiness` | 公開サービス機能。カメラ本体のCMS操作ではない |
| QR/リンク/WeChat等への共有 | PublicLive等 | OS共有・外部アプリとクラウドリンク |
| ユーザー登録・ログイン・ログアウト | `IAccountAuthBusiness` | メール／電話／認証コード／サードパーティー／企業等の複数経路 |
| メール・電話の結付け・変更・確認 | 同上 | アカウントAPI。実ユーザー情報を操作していない |
| パスワード・ユーザー名変更・プロフィール | 同上 | アカウント操作。未実行 |
| QRログイン確認・WeChat状態等 | 同上 | 外部認証。nativeの鍵・署名生成を公開資料に複製しない |
| 端末内キャプチャー／録画一覧・削除 | `IUserCenterBusiness` | スマホのファイル。カメラSDの初期化・録画削除と別 |
| 設定記憶、初回ガイド、通知・低電池表示 | `IPreferenceBusiness` | 端末内のPreference。カメラに送る設定ではないものを含む |
| フィードバック、ヘルプ、統計、セットアップログ | Account/Statistics、各案内画面 | サーバーへの送信を伴うものがある。今回は送信なし |

## 7. 保存処理・「getだから安全」ではない理由

`CmsNetUtil.setDevParam:109–172`には、main=75の成功後に**`65/3`を自動送信**する処理がある。ただしsub=**54/64/62/42は自動保存の例外**。

- 54：ライブURL要求。取得という名前でも`75/54`。
- 64：録画再生制御。
- 62：NAS設定。状態読返しはあるが、このラッパーの自動save対象外。
- 42：HTTP/カーネル更新要求。
- `setCodeGrade`と`setRecordType`にはさらに明示的`65/3`がある。そのため、ラッパー内の保存と外側の保存の両方を考慮する。
- `formatSdc`は`75/83`で、ラッパーの例外に入っていない。名前やmain番号の推測で一括実行しない。
- `cmsExecCMD`と`setDevParam`は別入口。main/subの代入一覧だけでは、認証・応答待ち・saveの全動作は表現できない。
- SMBのフォルダー取得も、書込み確認としてファイルを作成・削除し得る。

本資料の一覧を自動的に順番に送信する「全コマンド試験器」は作っていない。未知のデータ構造に0埋めSETしたり、SD初期化・更新・権限変更を一括試行したりしていない。

## 8. 今回の手法、網羅性と限界

1. app/SDK/CMS/player領域のJava 1060ファイル、うちUI 302ファイルを走査。
2. Business/HTTP/LAN契約とSDK公開／nativeラッパー入口を列挙。コールバックやキャッシュをユーザー機能と数えない。
3. UIをファイル全体で走査し、匿名リスナーやフィールド初期化内の呼出しも採取。画面メソッド本体だけを探す方式の漏れを修正。
4. HTTP動詞・パス・フォームキー、CMSのmain/sub代入とコンストラクター引数を記録。定数を数値・APIパスに解決できない場合は未解決として残す。
5. 夜間・露出はJavaだけでなくレイアウトXML・英語表示文字列で照合。表示上の"high/low"を勝手に別パラメーターへ読み替えない。
6. PTZ、ストレージ、save、送話などの要所は、抽出結果に加えて実装本体を目視追跡。
7. 解析ツールの合成Javaテスト10件とリポジトリ全体のオフライン検査。実機には通常中継の受信チェック以外の追加操作をしない。

### 断定していないこと

- 索引はJavaコンパイラーの型解析ではなく**字句ベース**。同名メソッドの参照は追跡の手掛かりで、到達可能性の形式的証明ではない。反射・動的Proxy・native内部・サーバー側の動作は全解析していない。
- 1060ファイルは選択したアプリ/SDK/CMS/player領域。APK全体の全サードパーティーコードを完全解析したという意味ではない。
- JADXの未復元箇所が1つある：`WifiNetworkManager.getScanWifResult`。Wi-Fiスキャン結果整理の擬似命令は残るが、完全なJava復元・全分岐の検証とはしない。
- 契約宣言の漏れ検査はBusiness/HTTP/LANインターフェースが対象。SDK公開／nativeラッパー入口の存在を全native機能の網羅とはしない。
- 画面には機種・オンライン状態・所有権・クラウドプラン・録画媒体による表示制限がある。PTZ、433、NAS、環境センサー等をこの個体が実装しているとは未確認。
- UART/LinuxコンソールをONにする操作は今回の追跡でも見つかっていない。9600bpsのシリアル設定をUARTシェル有効化と読み替えない。
- 再生できている通常経路は**Linuxがカメラから取得するRTMP pull**。カメラ自身がLinuxへネイティブ自動pushする実装が完成したという意味ではない。

## 9. 次に実機で確認する場合の順番

1. 稼働中の映像・音声を確認した上で、既知の読取りコマンドとハードウェア有無を絞って確認する。
2. 復元できる設定をバックアップし、LED・反転・露出・画質を**一項目ずつ**変更・読返し・元に戻す。
3. PTZは可動機構がある個体だけ。NASは専用のテスト共有、SDは消してよい媒体だけで試す。
4. スピーカーは送話URL・認証と実音を別々に検証。クラウドUI再現は認証・状態取得・設定配送の実装を別課題にする。
5. SD初期化、ファーム更新、クラウド登録解除、共有権限変更は明示的に実施対象を確認してから。

今回の調査完了後の再生フレーム数・復号結果、サービス状態、Git保存状況は`app-operations-summary.json`と検証記録を参照。
