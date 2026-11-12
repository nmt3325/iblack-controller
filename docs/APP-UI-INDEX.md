# アプリUI操作索引

[操作の説明](APP-OPERATIONS.md) / [API索引](APP-API-INDEX.md)

クリック注釈とview IDのswitchを機械的に抽出。タッチ、ジェスチャー、動的生成、権限・機種条件を完全に網羅する証明ではない。
同名API参照を併記するが、Javaの型解決・画面の実機到達性は保証しない。

## com/iermu/ui/activity/ActivityIntentHelper.java


参照：L25 `grantShare` (doConfirm)

## com/iermu/ui/activity/FourLiveActivity.java

- L122 OnClick_annotation: `four_live_back`
- L125 view_id_switch_case: `four_live_back`
- L128 view_id_switch_case: `imageViewClose`

参照：L83 `registerListener` (onCreate); L87 `getMineCamList` (onCreate); L88 `getAuthorizeCamList` (onCreate); L92 `getCategoryList` (onCreate); L99 `isInFourLiveFirst` (onCreate); L100 `setIsInFourLiveFirst` (onCreate); L135 `getMineCamList` (getPageList); L153 `getMineCamList` (onMimeCamChanged); L161 `unRegisterListener` (onDestroy)

## com/iermu/ui/activity/MainActivity.java

- L233 view_id_switch_case: `maintab_iermu`
- L236 view_id_switch_case: `maintab_message`
- L252 view_id_switch_case: `maintab_public`
- L255 view_id_switch_case: `maintab_personal`

参照：L86 `handleMessage` (handleMessage); L99 `setSystemNotice` (onReceive); L101 `setAlarmNotice` (onReceive); L103 `getMineCamList` (onReceive); L114 `setSystemNotice` (onReceive); L116 `setAlarmNotice` (onReceive); L120 `pushNewAlarmReceiver` (onReceive); L122 `pushNewSystemNoticeReceiver` (onReceive); L173 `registerListener` (onCreate); L174 `registerListener` (onCreate); L175 `registerListener` (onCreate); L176 `registerListener` (onCreate); L211 `unRegisterListener` (onDestroy); L212 `unRegisterListener` (onDestroy); L213 `unRegisterListener` (onDestroy); L214 `unRegisterListener` (onDestroy); L224 `removeUpgradeStatus` (exit); L242 `setAlarmNotice` (onCheckedChanged); L244 `setSystemNotice` (onCheckedChanged); L295 `isAlarmNotice` (initMainTabFragment); L297 `isSystemNotice` (initMainTabFragment); L297 `isAlarmNotice` (initMainTabFragment); L299 `getMineCamList` (initMainTabFragment); L348 `grantShare` (doConfirm); L368 `isLogin` (playShareDev); L384 `isLogin` (getAuthCode); L474 `logout` (onClick); L502 `isLogin` (handleIntent); L522 `isQDLTLogin` (handleIntent); L546 `markReadSystemNotice` (handleIntent); L554 `getBaiduUserName` (onBaiduStatusToLogin); L566 `logout` (onClick); L578 `getBaiduUserName` (onBaiduTokenInvalid); L591 `logout` (onClick); L613 `logout` (onClick)

## com/iermu/ui/activity/NotificationPushReceiver.java


参照：L51 `setAlarmNotice` (onReceive); L54 `setSystemNotice` (onReceive)

## com/iermu/ui/activity/OrderWebActivity.java

- L74 view_id_switch_case: `set_back`
- L77 view_id_switch_case: `error_btn`

## com/iermu/ui/activity/SplashActivity.java

- L123 OnClick_annotation: `new_poster`
- L126 view_id_switch_case: `new_poster`

参照：L62 `isQDLTLogin` (onCreate); L70 `isLogin` (onCreate); L74 `registerListener` (onCreate); L84 `authThird` (onCreate); L93 `registerListener` (showNewPoster); L94 `registerListener` (showNewPoster); L96 `getPosterImgUrl` (showNewPoster); L97 `getPosterStartTime` (showNewPoster); L98 `getPosterEndTime` (showNewPoster); L111 `setPosterImgUrl` (showNewPoster); L114 `getNewPoster` (showNewPoster); L127 `getPosterWebUrl` (onClick); L128 `getPosterAction` (onClick); L150 `isLogin` (delay); L165 `unRegisterListener` (onDestroy); L166 `unRegisterListener` (onDestroy)

## com/iermu/ui/activity/WebActivity.java

- L389 view_id_switch_case: `set_back`
- L397 view_id_switch_case: `action_finish`
- L400 view_id_switch_case: `error_btn`

参照：L191 `getAccessToken` (showWeb); L193 `getCamLive` (showWeb); L244 `getUid` (showWeb); L245 `getAccessToken` (showWeb); L282 `getPosterWebUrl` (showWeb); L336 `getAccessToken` (showWeb); L450 `unRegisterListener` (onDestroy); L469 `isLogin` (run); L538 `registerListener` (shouldOverrideUrlLoading); L539 `getThirdConnect` (shouldOverrideUrlLoading)

## com/iermu/ui/activity/login/BaiDuLoginActivity.java

- L201 view_id_switch_case: `set_back`
- L205 view_id_switch_case: `error_btn`

参照：L167 `registerListener` (onResume); L173 `unRegisterListener` (onPause); L182 `unRegisterListener` (onDestroy); L348 `getAccessTokenAsQDLT` (handleWebviewInterceptRequest); L350 `getAccessToken` (handleWebviewInterceptRequest)

## com/iermu/ui/activity/login/CompanyLoginActivity.java

- L116 OnClick_annotation: `actionbar_back`, `clear_text_img`, `login_pwd`, `clear_pwd_img`, `login_pass_show`, `login_btn`
- L120 view_id_switch_case: `actionbar_back`
- L123 view_id_switch_case: `clear_text_img`
- L127 view_id_switch_case: `clear_pwd_img`
- L130 view_id_switch_case: `login_pass_show`
- L138 view_id_switch_case: `login_btn`

参照：L159 `companyLogin` (onClick); L176 `registerListener` (onResume); L182 `unRegisterListener` (onPause)

## com/iermu/ui/activity/login/MobileCodeLoginActivity.java

- L122 OnClick_annotation: `actionbar_back`, `get_message_code`, `clear_text_img`, `login_pwd`, `login_btn`, `use_pwd_login`, `register_btn`, `login_baidu_text`, `company_login`
- L89 view_id_switch_case: `login_phone_num`
- L96 view_id_switch_case: `login_code`
- L129 view_id_switch_case: `actionbar_back`
- L132 view_id_switch_case: `clear_text_img`
- L135 view_id_switch_case: `get_message_code`
- L148 view_id_switch_case: `login_btn`
- L164 view_id_switch_case: `register_btn`
- L168 view_id_switch_case: `company_login`
- L171 view_id_switch_case: `login_baidu_text`
- L185 view_id_switch_case: `use_pwd_login`

参照：L117 `registerListener` (onCreate); L118 `registerListener` (onCreate); L141 `getMobileVerifyToLogin` (onClick); L154 `useMobileVerifyCodeLogin` (onClick); L195 `unRegisterListener` (onDestroy); L196 `unRegisterListener` (onDestroy)

## com/iermu/ui/activity/login/MobileLoginActivity.java

- L126 OnClick_annotation: `actionbar_back`, `clear_text_img`, `login_pwd`, `clear_pwd_img`, `login_pass_show`, `login_btn`, `use_sms_login`, `forget_password`, `register_btn`, `login_bd_tip`, `login_baidu_text`, `company_login`
- L93 view_id_switch_case: `login_pwd`
- L97 view_id_switch_case: `login_phone_num`
- L131 view_id_switch_case: `actionbar_back`
- L134 view_id_switch_case: `clear_text_img`
- L137 view_id_switch_case: `clear_pwd_img`
- L140 view_id_switch_case: `login_pass_show`
- L148 view_id_switch_case: `login_btn`
- L173 view_id_switch_case: `register_btn`
- L177 view_id_switch_case: `use_sms_login`
- L181 view_id_switch_case: `forget_password`
- L189 view_id_switch_case: `company_login`
- L192 view_id_switch_case: `login_baidu_text`
- L209 view_id_switch_case: `login_bd_tip`

参照：L121 `getMobileLoginFirst` (onCreate); L162 `mobileLogin` (onClick); L211 `setMobileLoginFirst` (onClick); L219 `registerListener` (onResume); L225 `unRegisterListener` (onPause)

## com/iermu/ui/activity/login/MobileRegisterActivity.java

- L181 OnClick_annotation: `actionbar_back`, `login_txt`, `get_message_code`, `register_pwd_show`, `register_btn`, `clear_nickname_img`, `clear_num_img`, `clear_pwd_img`
- L123 view_id_switch_case: `register_nickname`
- L128 view_id_switch_case: `register_phone_num`
- L133 view_id_switch_case: `register_code`
- L138 view_id_switch_case: `register_pwd`
- L189 view_id_switch_case: `actionbar_back`
- L192 view_id_switch_case: `get_message_code`
- L205 view_id_switch_case: `clear_pwd_img`
- L208 view_id_switch_case: `register_btn`
- L248 view_id_switch_case: `login_txt`
- L253 view_id_switch_case: `clear_nickname_img`
- L256 view_id_switch_case: `clear_num_img`
- L259 view_id_switch_case: `register_pwd_show`

参照：L168 `registerListener` (onCreate); L169 `registerListener` (onCreate); L170 `registerListener` (onCreate); L176 `unRegisterListener` (onDestroy); L177 `unRegisterListener` (onDestroy); L178 `unRegisterListener` (onDestroy); L198 `getMobileVerifyToRegister` (onClick); L237 `mobileRegister` (onClick); L346 `mobileLogin` (onRegister)

## com/iermu/ui/activity/login/SetMobilePwdActivity.java

- L80 OnClick_annotation: `pwd_hide_show`, `confirm`, `jump`
- L84 view_id_switch_case: `jump`
- L87 view_id_switch_case: `pwd_hide_show`
- L95 view_id_switch_case: `confirm`

参照：L71 `registerListener` (onCreate); L77 `unRegisterListener` (onDestroy); L106 `setMobileLoginPassword` (onClick)

## com/iermu/ui/activity/login/VerifyMobileActivity.java

- L142 OnClick_annotation: `actionbar_back`, `get_message_code`, `commit_confirm`, `clear_text_img`, `jump`
- L87 view_id_switch_case: `mobile_num`
- L94 view_id_switch_case: `message_code`
- L147 view_id_switch_case: `actionbar_back`
- L150 view_id_switch_case: `clear_text_img`
- L153 view_id_switch_case: `get_message_code`
- L161 view_id_switch_case: `commit_confirm`
- L174 view_id_switch_case: `jump`

参照：L128 `registerListener` (onCreate); L129 `registerListener` (onCreate); L138 `unRegisterListener` (onDestroy); L139 `unRegisterListener` (onDestroy); L155 `getMobileVerify` (onClick); L168 `bindMobile` (onClick)

## com/iermu/ui/activity/login/WelcomeLoginActivity.java

- L73 OnClick_annotation: `login_btn`, `register_btn`, `have_a_look`
- L77 view_id_switch_case: `login_btn`
- L80 view_id_switch_case: `register_btn`
- L83 view_id_switch_case: `have_a_look`

## com/iermu/ui/activity/login/WelcomeLoginIntlActivity.java

- L257 OnClick_annotation: `login_btn`, `mail_login_back`, `login_baidu_mail`, `hide_or_show_btn`, `mail_close`, `fast_register_mail`, `login_baidu`, `mail_login`, `have_look`, `fast_register`, `register_login_back`, `mail_close_register`, `hide_or_show_btn_register`, `login_btn_register`, `forget_password`, `company_login`
- L260 view_id_switch_case: `login_btn`
- L276 view_id_switch_case: `login_baidu`
- L291 view_id_switch_case: `mail_login`
- L297 view_id_switch_case: `fast_register`
- L304 view_id_switch_case: `have_look`
- L307 view_id_switch_case: `forget_password`
- L310 view_id_switch_case: `company_login`
- L313 view_id_switch_case: `mail_login_back`
- L330 view_id_switch_case: `mail_close`
- L333 view_id_switch_case: `hide_or_show_btn`
- L342 view_id_switch_case: `login_baidu_mail`
- L352 view_id_switch_case: `fast_register_mail`
- L359 view_id_switch_case: `register_login_back`
- L378 view_id_switch_case: `mail_close_register`
- L381 view_id_switch_case: `hide_or_show_btn_register`
- L390 view_id_switch_case: `login_btn_register`

参照：L206 `registerListener` (onCreate); L247 `registerListener` (onResume); L253 `unRegisterListener` (onPause); L270 `registerAccount` (onClick); L416 `emailRegister` (onClick); L640 `unRegisterListener` (onDestroy); L641 `unRegisterListener` (onDestroy); L714 `registerAccount` (onRegister)

## com/iermu/ui/adapter/AlarmMessageGridAdapter.java


参照：L111 `getCount` (isSelectAll); L123 `getCount` (notifySelOrUnSelectAll); L125 `getCount` (notifySelOrUnSelectAll)

## com/iermu/ui/adapter/AlarmSettingAdapter.java


参照：L104 `getCamAlarm` (getView); L106 `getAlarmCron` (getView); L107 `getCamAlarm` (getView); L108 `getCamLive` (getView)

## com/iermu/ui/adapter/DeviceSearchAdapter.java


参照：L75 `isOffline` (initMyCamData)

## com/iermu/ui/adapter/ItemFourLiveAdapter.java


参照：L69 `registerListener` (<constructor_or_field_initializer>); L124 `openLiveMedia` (onClick); L142 `powerAndOpenLiveMedia` (onClick); L179 `isConnected` (onBindViewHolder); L195 `isOffline` (onBindViewHolder); L234 `getCamLive` (onBindViewHolder); L242 `openLiveMedia` (onBindViewHolder); L252 `isConnected` (onBindViewHolder); L255 `isOffline` (onBindViewHolder); L265 `openLiveMedia` (onBindViewHolder); L285 `openLiveMedia` (onBindViewHolder); L331 `getCamLive` (onViewAttachedToWindow); L332 `isOffline` (onViewAttachedToWindow); L347 `openLiveMedia` (onViewAttachedToWindow); L460 `handleMessage` (handleMessage); L468 `getLiveMedia` (handleMessage); L476 `isOffline` (handleMessage); L537 `isConnected` (openVideoView); L539 `isOffline` (openVideoView); L546 `openLiveMedia` (openVideoView)

## com/iermu/ui/adapter/MainIermuAdapter.java

- L322 view_id_switch_case: `drop_auth_dev`
- L325 view_id_switch_case: `collect_cam`
- L332 view_id_switch_case: `collect_left`
- L339 view_id_switch_case: `collect_right`
- L346 view_id_switch_case: `cam_setting`
- L349 view_id_switch_case: `cam_homer`
- L355 view_id_switch_case: `cam_share`
- L358 view_id_switch_case: `cam_record`

参照：L155 `isOffline` (initMyCamData); L214 `isOffline` (initAuthCamData); L251 `isOffline` (initCollectData); L477 `getCamLive` (openPublicFragment); L495 `getUpgradeCount` (review_upgrade_loading); L496 `getUpgradeStatus` (review_upgrade_loading)

## com/iermu/ui/adapter/PictureAdapter.java


参照：L38 `getBaiduAccessToken` (<constructor_or_field_initializer>)

## com/iermu/ui/adapter/PublicChannelAdapter.java


参照：L83 `isOffline` (getView); L104 `getCount` (getItemIds)

## com/iermu/ui/adapter/PublicLiveCommentsAdapter.java


参照：L99 `getUid` (getView); L109 `getUid` (getView)

## com/iermu/ui/adapter/RecordDateGridAdapter.java


参照：L44 `getCamLive` (setDates)

## com/iermu/ui/adapter/ScreenFilmAdapter.java


参照：L48 `getBaiduAccessToken` (<constructor_or_field_initializer>); L133 `getCount` (notifyDataSetChanged)

## com/iermu/ui/adapter/SensorAdapter.java

- L130 view_id_switch_case: `sensor_btn`

参照：L68 `registerListener` (getView); L76 `get433DevTestStatus` (getView); L113 `unRegisterListener` (onUpdate433Status); L141 `update433DevStatus` (onCheckedChanged)

## com/iermu/ui/adapter/SensorAlarmAdapter.java


参照：L53 `setSensorLowPowerAlarmGone` (onClick)

## com/iermu/ui/fragment/AboutFragment.java

- L95 OnClick_annotation: `menu_feedback`, `custom_phone_or_email`, `person_offical`, `cam_version`, `dev_update_item`
- L99 view_id_switch_case: `menu_feedback`
- L103 view_id_switch_case: `custom_phone_or_email`
- L129 view_id_switch_case: `person_offical`
- L132 view_id_switch_case: `dev_update_item`

参照：L83 `getAlertedUpdateVersion` (onCreateView)

## com/iermu/ui/fragment/BaseFragment.java


参照：L736 `invoke` (disableABCShowHideAnimation)

## com/iermu/ui/fragment/FourLiveTipFragment.java

- L32 OnClick_annotation: `four_live_content`, `iermu_bg`
- L35 view_id_switch_case: `four_live_content`

参照：L36 `setIsFourLiveInsertFirst` (onClick); L45 `setIsFourLiveInsertFirst` (onDestroy)

## com/iermu/ui/fragment/MainIermuEmptyFragment.java

- L78 OnClick_annotation: `add_cam`, `grand_code_has_received`
- L81 view_id_switch_case: `add_cam`
- L85 view_id_switch_case: `grand_code_has_received`

参照：L145 `getBaiduUid` (setBindBdAccount)

## com/iermu/ui/fragment/MainIermuFragment.java

- L172 OnClick_annotation: `actionbar_adddev`, `actionbar_title`, `device_category`, `grand_code_has_received`, `add_cam`, `four_live_img`
- L175 view_id_switch_case: `actionbar_title`
- L179 view_id_switch_case: `four_live_img`
- L191 view_id_switch_case: `actionbar_adddev`
- L195 view_id_switch_case: `device_category`

参照：L70 `isCategoryAllNull` (onCreateActionBar); L90 `unRegisterListener` (onHiddenChanged); L92 `registerListener` (onHiddenChanged); L112 `getListCategory` (onCreateView); L121 `registerListener` (onCreateView); L125 `registerListener` (onCreateView); L126 `isFourLiveInsertFirst` (onCreateView); L155 `unRegisterListener` (onDestroy); L156 `unRegisterListener` (onDestroy); L180 `isConnected` (onClick); L224 `isCategoryAllNull` (showEmpty)

## com/iermu/ui/fragment/MainMessageFragment.java

- L201 OnClick_annotation: `live_setting`, `add_cam_btn`, `buttonOpenAlarm`, `action_bar_btn_left`, `action_bar_btn_right`, `refresh_view`
- L204 view_id_switch_case: `action_bar_btn_left`
- L210 view_id_switch_case: `action_bar_btn_right`
- L217 view_id_switch_case: `live_setting`
- L218 view_id_switch_case: `buttonOpenAlarm`
- L222 view_id_switch_case: `refresh_view`
- L226 view_id_switch_case: `add_cam_btn`

参照：L152 `registerListener` (onCreateView); L153 `registerListener` (onCreateView); L154 `registerListener` (onCreateView); L155 `registerListener` (onCreateView); L156 `syncNewAlarmNotice` (onCreateView); L157 `getMineCamCount` (onCreateView); L158 `syncNewSystemNotice` (onCreateView); L168 `syncNewAlarmNotice` (onResume); L178 `getSensorLowPowerAlarmList` (initSensorLowPowerView); L197 `syncNewAlarmNotice` (onHiddenChanged); L205 `setAlarmNotice` (onClick); L211 `setSystemNotice` (onClick); L215 `syncNewSystemNotice` (onClick); L224 `syncNewSystemNotice` (onClick); L250 `markReadSystemNotice` (onItemClick); L259 `unRegisterListener` (onDestroyView); L260 `unRegisterListener` (onDestroyView); L261 `unRegisterListener` (onDestroyView); L286 `getAlarmNoticeList` (refreshView); L293 `getMineCamLiveCount` (refreshView); L295 `getCount` (refreshView); L301 `getCount` (refreshView); L320 `getSystemNotices` (refreshMessageListView); L322 `getCount` (refreshMessageListView); L326 `getCount` (refreshMessageListView); L350 `isSystemNotice` (refreshActionBar); L351 `isAlarmNotice` (refreshActionBar); L363 `getMineCamLiveCount` (refreshActionBar); L364 `getCount` (refreshActionBar); L372 `getAlarmItem` (isPushOpenForDevices); L376 `getCamAlarm` (isPushOpenForDevices); L395 `syncNewAlarmNotice` (onRefresh); L398 `syncNewSystemNotice` (onRefresh)

## com/iermu/ui/fragment/MainPersonalFragment.java

- L179 OnClick_annotation: `menu_iermu_buy`, `feed_back`, `menu_cvr_buy`, `menu_set`, `FAQ_VIEW`, `menu_about`, `photo_view`, `film_edit`, `cam_view`, `collection_photo`, `my_account`, `img_photo`, `qr_code_scan`, `btn_logout`
- L183 view_id_switch_case: `menu_cvr_buy`
- L186 view_id_switch_case: `img_photo`
- L187 view_id_switch_case: `my_account`
- L206 view_id_switch_case: `btn_logout`
- L209 view_id_switch_case: `menu_iermu_buy`
- L212 view_id_switch_case: `collection_photo`
- L221 view_id_switch_case: `FAQ_VIEW`
- L224 view_id_switch_case: `feed_back`
- L228 view_id_switch_case: `menu_about`
- L232 view_id_switch_case: `qr_code_scan`
- L237 view_id_switch_case: `photo_view`
- L241 view_id_switch_case: `film_edit`
- L250 view_id_switch_case: `cam_view`

参照：L114 `registerListener` (onCreateView); L115 `registerListener` (onCreateView); L116 `registerListener` (onCreateView); L117 `getThirdConnect` (onCreateView); L119 `registerListener` (onCreateView); L120 `getAllPicAndRecord` (onCreateView); L121 `getMineCamCount` (onCreateView); L132 `getScreenClip` (initPhoto); L146 `getAllPicAndRecord` (onHiddenChanged); L153 `registerListener` (onHiddenChanged); L156 `unRegisterListener` (onHiddenChanged); L164 `getUserInfo` (onViewCreated); L173 `unRegisterListener` (onDestroyView); L174 `removeUserInfoListener` (onDestroyView); L175 `unRegisterListener` (onDestroyView); L176 `unRegisterListener` (onDestroyView); L268 `logout` (onClick); L289 `getMyCamCount` (refreshView)

## com/iermu/ui/fragment/MainPublicFragment.java

- L192 OnClick_annotation: `recommend_cam`, `hot_cam`, `new_cam`
- L195 view_id_switch_case: `recommend_cam`
- L199 view_id_switch_case: `hot_cam`
- L203 view_id_switch_case: `new_cam`

## com/iermu/ui/fragment/MineCommonFragment.java

- L253 OnClick_annotation: `actionbar_adddev`, `add_cam`, `grand_code_has_received`, `error_btn`
- L256 view_id_switch_case: `actionbar_adddev`
- L257 view_id_switch_case: `add_cam`
- L261 view_id_switch_case: `grand_code_has_received`

参照：L114 `getUpgradeCount` (myOnClick); L115 `isOffline` (myOnClick); L119 `checkUpgradeVersion` (myOnClick); L132 `isOffline` (myOnClick); L139 `checkUpgradeVersion` (myOnClick); L143 `isOffline` (myOnClick); L190 `registerListener` (onCreateView); L191 `registerListener` (onCreateView); L192 `registerListener` (onCreateView); L193 `registerListener` (onCreateView); L194 `registerListener` (onCreateView); L195 `registerListener` (onCreateView); L199 `syncNewCamList` (onCreateView); L201 `syncCategoryList` (onCreateView); L203 `getCount` (onCreateView); L223 `unRegisterListener` (onDestroyView); L224 `unRegisterListener` (onDestroyView); L225 `unRegisterListener` (onDestroyView); L226 `unRegisterListener` (onDestroyView); L227 `unRegisterListener` (onDestroyView); L228 `unRegisterListener` (onDestroyView); L233 `isLoadedAllCamLive` (onScrollStateChanged); L234 `isLoadedAllCamLive` (onScrollStateChanged); L238 `syncOldCamList` (onScrollStateChanged); L240 `syncCategoryList` (onScrollStateChanged); L274 `getListCategory` (onRefresh); L276 `syncNewCamList` (onRefresh); L278 `syncCategoryList` (onRefresh); L288 `getCategoryList` (refreshAdapter); L291 `getCount` (refreshAdapter); L301 `getCategoryList` (clearUpgradeStatus); L303 `setUpgradeStatus` (clearUpgradeStatus); L304 `setUpgradeCount` (clearUpgradeStatus); L311 `getCategoryList` (onMimeCamChanged); L314 `getCount` (onMimeCamChanged); L322 `getUpgradeCount` (onItemClick); L330 `isOffline` (onItemClick); L334 `checkUpgradeVersion` (onItemClick); L348 `isOffline` (onItemClick); L352 `checkUpgradeVersion` (onItemClick); L360 `isOffline` (onItemClick); L380 `getCategoryList` (onGetCamListByCategory); L384 `getCount` (onGetCamListByCategory); L390 `getCount` (onGetCamListByCategory); L391 `getCount` (onGetCamListByCategory); L426 `dropGrantDevice` (onClick); L463 `checkCamFirmware` (doConfirm); L496 `setUpgradeStatus` (OnGetCamUpdateStatus); L506 `getUpgradeCount` (OnGetCamUpdateStatus); L514 `setUpgradeCount` (OnGetCamUpdateStatus); L532 `setUpgradeStatus` (OnGetCamUpdateStatus); L533 `setUpgradeCount` (OnGetCamUpdateStatus); L535 `syncNewCamList` (OnGetCamUpdateStatus); L538 `syncCategoryList` (OnGetCamUpdateStatus); L556 `setUpgradeStatus` (OnGetCamUpdateStatus); L557 `setUpgradeCount` (OnGetCamUpdateStatus); L559 `syncNewCamList` (OnGetCamUpdateStatus); L562 `syncCategoryList` (OnGetCamUpdateStatus); L567 `setUpgradeStatus` (OnGetCamUpdateStatus); L568 `setUpgradeCount` (OnGetCamUpdateStatus); L587 `setUpgradeStatus` (OnGetCamUpdateStatus); L588 `setUpgradeCount` (OnGetCamUpdateStatus); L589 `getUpgradeStatus` (OnGetCamUpdateStatus); L589 `getUpgradeCount` (OnGetCamUpdateStatus); L591 `syncNewCamList` (OnGetCamUpdateStatus); L594 `syncCategoryList` (OnGetCamUpdateStatus); L618 `checkCamFirmware` (onFirmwareUpdate); L632 `getCategoryList` (onGetCategoryList); L634 `getCount` (onGetCategoryList); L638 `getCount` (onGetCategoryList); L639 `getCount` (onGetCategoryList); L676 `isConnected` (loadingViewFragmentShow); L676 `getCount` (loadingViewFragmentShow)

## com/iermu/ui/fragment/NetExceptionFragment.java

- L67 OnClick_annotation: `error_btn`
- L70 view_id_switch_case: `error_btn`

参照：L73 `syncNewCamList` (onClick); L75 `getListCategory` (onClick); L78 `syncCategoryList` (onClick)

## com/iermu/ui/fragment/camseting/CamInfoFragment.java

- L118 OnClick_annotation: `camera_version`
- L121 view_id_switch_case: `camera_version`

参照：L102 `registerListener` (onCreateView); L103 `registerListener` (onCreateView); L104 `syncCamInfo` (onCreateView); L114 `unRegisterListener` (onDestroyView); L115 `unRegisterListener` (onDestroyView); L122 `getCamLive` (onClick); L123 `getUpgradeCount` (onClick); L128 `isOffline` (onClick); L132 `checkUpgradeVersion` (onClick); L146 `getCamLive` (doConfirm); L148 `checkCamFirmware` (doConfirm); L164 `getUid` (refreshView); L166 `getCamLive` (refreshView); L171 `isOffline` (refreshView); L217 `isOffline` (onCamSetting)

## com/iermu/ui/fragment/camseting/CamNightFragment.java

- L67 view_id_switch_case: `set_use_in`
- L71 view_id_switch_case: `set_use_out`
- L83 view_id_switch_case: `night_automatic`
- L87 view_id_switch_case: `night_close`
- L91 view_id_switch_case: `night_start`
- L103 view_id_switch_case: `night_mode_automatic`
- L107 view_id_switch_case: `night_hight`
- L111 view_id_switch_case: `night_low`

参照：L69 `setDevScene` (onClick); L73 `setDevScene` (onClick); L85 `setDevNightMode` (onClick); L89 `setDevNightMode` (onClick); L93 `setDevNightMode` (onClick); L105 `setDevExposeMode` (onClick); L109 `setDevExposeMode` (onClick); L113 `setDevExposeMode` (onClick); L141 `initData` (onCreateView); L145 `registerListener` (onCreateView); L146 `registerListener` (onCreateView); L147 `registerListener` (onCreateView); L148 `registerListener` (onCreateView); L150 `syncCamStatus` (onCreateView); L159 `unRegisterListener` (onDestroyView); L160 `unRegisterListener` (onDestroyView); L161 `unRegisterListener` (onDestroyView); L162 `unRegisterListener` (onDestroyView); L192 `getCamStatus` (refreshView); L323 `isOffline` (onCamSetting)

## com/iermu/ui/fragment/camseting/CloudRecInfoFragment.java

- L194 OnClick_annotation: `want_know_more_btn`, `update_cloud_btn`, `view_set_time`, `detail_btn`
- L197 view_id_switch_case: `want_know_more_btn`
- L201 view_id_switch_case: `detail_btn`
- L202 view_id_switch_case: `update_cloud_btn`
- L205 view_id_switch_case: `view_set_time`

参照：L167 `getCamLive` (onCreateView); L171 `registerListener` (onCreateView); L172 `registerListener` (onCreateView); L173 `registerListener` (onCreateView); L174 `getCamCvr` (onCreateView); L176 `getListCvrRecord` (onCreateView); L187 `syncCamCvr` (onCreateView); L207 `unRegisterListener` (onClick); L476 `getListLyyCvrRecordInfo` (refreshCvrRecordList); L496 `getCamCvr` (onCamSetting); L529 `setCvr` (onCheckedChanged); L541 `getCamCvr` (onSetCron); L558 `getCamCvr` (onHiddenChanged); L559 `registerListener` (onHiddenChanged); L567 `unRegisterListener` (onDestroyView); L568 `unRegisterListener` (onDestroyView); L569 `unRegisterListener` (onDestroyView); L580 `getCamLive` (inThisTime); L604 `getCamLive` (inThisWeekDay)

## com/iermu/ui/fragment/camseting/CloudRecServiceFragment.java

- L178 OnClick_annotation: `buy_cloud_btn`, `record_cloud_free_btn`, `record_cloud_event_btn`, `record_cloud_duration_btn`
- L181 view_id_switch_case: `record_cloud_free_btn`
- L184 view_id_switch_case: `record_cloud_event_btn`
- L187 view_id_switch_case: `record_cloud_duration_btn`
- L190 view_id_switch_case: `buy_cloud_btn`

参照：L114 `getCamLive` (onCreateView)

## com/iermu/ui/fragment/camseting/CloudStatusFragment.java


参照：L46 `registerListener` (onCreateView); L47 `syncCamCloud` (onCreateView); L60 `unRegisterListener` (onDestroy); L65 `getCamCloud` (refreshView)

## com/iermu/ui/fragment/camseting/EditCamNameFragment.java

- L137 OnClick_annotation: `actionbar_finish`, `actionbar_back`, `dev_name_close`
- L141 view_id_switch_case: `actionbar_back`
- L148 view_id_switch_case: `actionbar_finish`
- L161 view_id_switch_case: `dev_name_close`

参照：L111 `registerListener` (onCreateView); L113 `getCamLive` (onCreateView); L130 `unRegisterListener` (onDestroyView); L153 `updateCamName` (onClick)

## com/iermu/ui/fragment/camseting/OptionTimezoneFragment.java


参照：L79 `getCamTimezone` (onCreateView); L91 `registerListener` (onCreateView); L116 `unRegisterListener` (onDestroyView); L121 `getCamLive` (onItemClick); L122 `isOffline` (onItemClick); L127 `updateCamTimezone` (onItemClick)

## com/iermu/ui/fragment/camseting/RecordInfoFragment.java

- L237 OnClick_annotation: `buttonBuy`, `viewSetTime`, `buttonDetail`
- L240 view_id_switch_case: `viewSetTime`
- L247 view_id_switch_case: `buttonBuy`
- L248 view_id_switch_case: `buttonDetail`

参照：L148 `getCamLive` (onCreateView); L200 `registerListener` (onCreateView); L201 `getRecordList` (onCreateView); L203 `initData` (onCreateView); L204 `findRecordList` (onCreateView); L208 `registerListener` (onCreateView); L209 `getScreenClip` (onCreateView); L211 `getBaiduUserRecordClip` (onCreateView); L215 `registerListener` (onCreateView); L216 `registerListener` (onCreateView); L217 `getCamCvr` (onCreateView); L229 `syncCamCvr` (onCreateView); L258 `getCamCvr` (onHiddenChanged); L266 `unRegisterListener` (onDestroyView); L267 `unRegisterListener` (onDestroyView); L268 `unRegisterListener` (onDestroyView); L269 `unRegisterListener` (onDestroyView); L283 `setCvr` (onCheckedChanged); L294 `getCamCvr` (onCamSetting); L326 `getCamCvr` (onSetCron); L469 `getDayTimeList` (setRecordInfo); L476 `getRecordList` (setRecordInfo); L493 `getScreenClip` (setFildEditInfo)

## com/iermu/ui/fragment/camseting/SettingFragment.java

- L235 OnClick_annotation: `set_home`, `set_time`, `set_more`, `set_cloud`, `set_camera_message`, `nas_setting`, `sd_setting`, `local_record_setting`, `set_cancel_camera`, `set_restart_camera`, `upgrade_setting`, `wifi_view`, `auth_view`, `share_view`, `set_location`, `view_timezone`
- L239 view_id_switch_case: `auth_view`
- L244 view_id_switch_case: `set_home`
- L249 view_id_switch_case: `set_location`
- L254 view_id_switch_case: `view_timezone`
- L259 view_id_switch_case: `set_time`
- L268 view_id_switch_case: `set_more`
- L275 view_id_switch_case: `set_camera_message`
- L279 view_id_switch_case: `set_cloud`
- L293 view_id_switch_case: `nas_setting`
- L294 view_id_switch_case: `sd_setting`
- L295 view_id_switch_case: `local_record_setting`
- L304 view_id_switch_case: `upgrade_setting`
- L308 view_id_switch_case: `share_view`
- L312 view_id_switch_case: `set_restart_camera`
- L339 view_id_switch_case: `set_cancel_camera`
- L389 view_id_switch_case: `campower_btn`

参照：L150 `getCamLive` (onCreateView); L156 `registerListener` (onCreateView); L157 `registerListener` (onCreateView); L158 `registerListener` (onCreateView); L159 `registerListener` (onCreateView); L160 `registerListener` (onCreateView); L161 `registerListener` (onCreateView); L162 `registerListener` (onCreateView); L163 `registerListener` (onCreateView); L164 `syncCamCvr` (onCreateView); L165 `syncCamInfo` (onCreateView); L166 `syncGrantUsers` (onCreateView); L167 `syncCamPowerCron` (onCreateView); L168 `getStorage` (onCreateView); L180 `getStorage` (onHiddenChanged); L181 `registerListener` (onHiddenChanged); L184 `unRegisterListener` (onHiddenChanged); L188 `getCamLive` (refreshView); L193 `isOffline` (refreshView); L194 `isOffline` (refreshView); L211 `getGrantUser` (refreshView); L218 `getCamStorageInfo` (refreshRecordNasAndSd); L237 `getCamLive` (onClick); L255 `getCamLive` (onClick); L260 `isOffline` (onClick); L281 `getCamLive` (onClick); L297 `findCardAndNasInfo` (onClick); L306 `checkCamFirmware` (onClick); L324 `restartCamDev` (onClick); L346 `getCamLive` (onClick); L359 `dropCamDev` (onClick); L376 `unRegisterListener` (onDestroyView); L377 `unRegisterListener` (onDestroyView); L378 `unRegisterListener` (onDestroyView); L379 `unRegisterListener` (onDestroyView); L380 `unRegisterListener` (onDestroyView); L381 `unRegisterListener` (onDestroyView); L382 `unRegisterListener` (onDestroyView); L400 `powerCamDev` (onCheckedChanged); L452 `getCamLive` (toastCamStatus); L453 `isOffline` (toastCamStatus); L473 `getCamLive` (onCamSetting); L474 `getCamInfo` (onCamSetting); L490 `getCamUpdateStatus` (onCheckCamFirmware); L497 `getCamLive` (setUser); L509 `getCamCvr` (setCvrText); L510 `isOffline` (setCvrText); L527 `getGrantUser` (getGrantUser); L540 `getGrantUser` (onGrantUser); L549 `getCamLive` (hasNasRecord); L550 `getCamInfo` (hasNasRecord)

## com/iermu/ui/fragment/camseting/SettingMoreFragment.java

- L115 OnClick_annotation: `set_speed_manager`, `set_night_sun`
- L118 view_id_switch_case: `set_night_sun`
- L122 view_id_switch_case: `set_speed_manager`
- L144 view_id_switch_case: `camlight_btn`
- L147 view_id_switch_case: `caminvert_btn`
- L150 view_id_switch_case: `camaudio_btn`

参照：L83 `registerListener` (onCreateView); L84 `registerListener` (onCreateView); L85 `registerListener` (onCreateView); L86 `registerListener` (onCreateView); L87 `syncCamStatus` (onCreateView); L98 `getCamLive` (onCreateView); L108 `unRegisterListener` (onDestroyView); L109 `unRegisterListener` (onDestroyView); L110 `unRegisterListener` (onDestroyView); L111 `unRegisterListener` (onDestroyView); L130 `getCamStatus` (refreshView); L145 `setDevLight` (onCheckedChanged); L148 `setDevInvert` (onCheckedChanged); L151 `setDevAudio` (onCheckedChanged)

## com/iermu/ui/fragment/camseting/SpeedManagerFragment.java

- L139 OnClick_annotation: `write_btn`, `actionbar_back`, `actionbar_finish`
- L142 view_id_switch_case: `actionbar_back`
- L145 view_id_switch_case: `actionbar_finish`
- L186 view_id_switch_case: `write_btn`

参照：L79 `registerListener` (onCreateView); L80 `registerListener` (onCreateView); L93 `unRegisterListener` (onDestroyView); L94 `unRegisterListener` (onDestroyView); L122 `getCamStatus` (refreshView); L126 `getCamInfo` (refreshView); L193 `setCamMaxspeed` (setSpeed); L226 `isOffline` (onCamSetting)

## com/iermu/ui/fragment/camseting/alarm/AlarmItemSettingFragment.java

- L187 OnClick_annotation: `set_alarm_time`, `alarm_move_view`, `alarm_audio_view`, `add_dev_433_more`, `dev_433_more`, `set_contact_phone`
- L193 view_id_switch_case: `set_contact_phone`
- L197 view_id_switch_case: `set_alarm_time`
- L202 view_id_switch_case: `alarm_move_view`
- L207 view_id_switch_case: `alarm_audio_view`
- L212 view_id_switch_case: `dev_433_more`
- L217 view_id_switch_case: `add_dev_433_more`
- L381 view_id_switch_case: `message_alarm_notice`
- L384 view_id_switch_case: `message_move_btn`
- L387 view_id_switch_case: `message_audio_btn`

参照：L136 `getCamLive` (onResume); L138 `get433alarmList` (onResume); L157 `getCamLive` (onCreateView); L162 `registerListener` (onCreateView); L163 `registerListener` (onCreateView); L164 `registerListener` (onCreateView); L165 `registerListener` (onCreateView); L166 `registerListener` (onCreateView); L167 `registerListener` (onCreateView); L168 `registerListener` (onCreateView); L169 `syncCamAlarm` (onCreateView); L178 `unRegisterListener` (onHiddenChanged); L179 `unRegisterListener` (onHiddenChanged); L180 `unRegisterListener` (onHiddenChanged); L182 `initData` (onHiddenChanged); L183 `registerListener` (onHiddenChanged); L189 `isConnected` (onClick); L229 `unRegisterListener` (onDestroyView); L230 `unRegisterListener` (onDestroyView); L231 `unRegisterListener` (onDestroyView); L232 `unRegisterListener` (onDestroyView); L233 `unRegisterListener` (onDestroyView); L234 `unRegisterListener` (onDestroyView); L235 `unRegisterListener` (onDestroyView); L239 `initData` (refreshView); L248 `getCamAlarm` (initData); L249 `getAlarmCron` (initData); L291 `getCamLive` (initSenserList); L293 `getSensorListByDeviceId` (initSenserList); L308 `getCamAlarm` (initSwitchBtn); L335 `getCamAlarm` (getLevel); L401 `setAlarmPush` (setAlarmType); L411 `setAlarmMove` (setAlarmType); L421 `setAlarmAudio` (setAlarmType); L450 `statPushFail` (onSetCamAlarm); L476 `initData` (onSetAlarmNotice); L488 `getCamAlarm` (onSetAlarmMove); L500 `getCamAlarm` (onSetAlarmAudio)

## com/iermu/ui/fragment/camseting/alarm/AlarmLevelFragment.java

- L126 OnClick_annotation: `actionbar_back`, `actionbar_finish`, `level`, `height_level`, `low_level`
- L129 view_id_switch_case: `actionbar_back`
- L132 view_id_switch_case: `actionbar_finish`
- L135 view_id_switch_case: `height_level`
- L139 view_id_switch_case: `level`
- L143 view_id_switch_case: `low_level`

参照：L98 `getCamAlarm` (initView); L114 `registerListener` (initView); L115 `registerListener` (initView); L122 `unRegisterListener` (onDestroyView); L123 `unRegisterListener` (onDestroyView); L152 `getCamAlarm` (setAlarmLevel); L160 `setAlarmMove` (setAlarmLevel); L173 `setAlarmAudio` (setAlarmLevel)

## com/iermu/ui/fragment/camseting/alarm/AlarmListSettingFragment.java

- L77 view_id_switch_case: `alarm_start`
- L80 view_id_switch_case: `viewClose`
- L83 view_id_switch_case: `viewReset`
- L86 view_id_switch_case: `textViewGotoOpen`
- L96 view_id_switch_case: `alarm_start`
- L99 view_id_switch_case: `viewClose`
- L102 view_id_switch_case: `viewReset`
- L105 view_id_switch_case: `textViewGotoOpen`

参照：L63 `getUpgradeCount` (myOnClick); L64 `getCamLive` (myOnClick); L65 `isOffline` (myOnClick); L67 `checkUpgradeVersion` (myOnClick); L69 `isOffline` (myOnClick); L72 `checkUpgradeVersion` (myOnClick); L91 `isOffline` (myOnClick); L139 `getAlarmItem` (onCreateView); L143 `registerListener` (onCreateView); L144 `registerListener` (onCreateView); L145 `registerListener` (onCreateView); L146 `registerListener` (onCreateView); L147 `syncAlarmItem` (onCreateView); L148 `sync433AlarmItem` (onCreateView); L170 `getAlarmItem` (onHiddenChanged); L179 `getCamLive` (onCloseClick); L183 `isOffline` (onCloseClick); L192 `isConnected` (onClick); L196 `setAlarmPush` (onClick); L219 `getCamLive` (onResetClick); L223 `isOffline` (onResetClick); L237 `getCamLive` (onGotoOpenClick); L241 `isOffline` (onGotoOpenClick); L254 `unRegisterListener` (onDestroyView); L255 `unRegisterListener` (onDestroyView); L256 `unRegisterListener` (onDestroyView); L257 `unRegisterListener` (onDestroyView); L274 `getAlarmItem` (onSetAlarmNotice); L281 `getAlarmItem` (onAlarmItemChanged); L290 `getAlarmItem` (onSensorItemChange); L313 `sendListener` (doConfirm)

## com/iermu/ui/fragment/camseting/alarm/AlarmMsgSettingFragment.java

- L166 OnClick_annotation: `alarm_sound_frequency_set_ll`, `wechat_alarm_no_start_ll`
- L169 view_id_switch_case: `alarm_sound_frequency_set_ll`
- L173 view_id_switch_case: `wechat_alarm_no_start_ll`
- L184 view_id_switch_case: `receive_new_msg_switch_btn`
- L190 view_id_switch_case: `sound_remind_switch_btn`
- L195 view_id_switch_case: `vibrate_remind_switch_btn`
- L200 view_id_switch_case: `wechat_push_switch_btn`

参照：L101 `registerListener` (onCreateView); L102 `registerListener` (onCreateView); L103 `registerListener` (onCreateView); L104 `getCamWeChatPushStatus` (onCreateView); L105 `getThirdConnect` (onCreateView); L211 `updateCamAlarmStatus` (onCheckedChanged); L237 `unRegisterListener` (onDestroy); L238 `unRegisterListener` (onDestroy); L239 `unRegisterListener` (onDestroy)

## com/iermu/ui/fragment/camseting/alarm/SoundFreqSettingFragment.java

- L91 OnClick_annotation: `per_one_minute_ll`, `per_five_minute_ll`, `per_ten_minute_ll`, `per_thirty_minute_ll`
- L94 view_id_switch_case: `per_one_minute_ll`
- L97 view_id_switch_case: `per_five_minute_ll`
- L100 view_id_switch_case: `per_ten_minute_ll`
- L103 view_id_switch_case: `per_thirty_minute_ll`

## com/iermu/ui/fragment/camseting/alarmcontact/ContactPhoneListFragment.java

- L94 OnClick_annotation: `actionbar_back`, `actionbar_finish`
- L97 view_id_switch_case: `actionbar_back`
- L100 view_id_switch_case: `actionbar_finish`

参照：L74 `getContactList` (onHiddenChanged); L86 `registerListener` (onCreateView); L87 `registerListener` (onCreateView); L88 `getContactList` (onCreateView); L89 `deleteContact` (onCreateView); L115 `unRegisterListener` (onDestroy); L116 `unRegisterListener` (onDestroy); L167 `getCount` (onMenuItemClick); L170 `deleteContact` (onMenuItemClick); L188 `getContactList` (onDeleteContact)

## com/iermu/ui/fragment/camseting/alarmcontact/EditContactPoneFragment.java

- L109 OnClick_annotation: `save_perfect`, `actionbar_back`, `contact_name_close`, `contact_phone_close`, `check_btn`, `actionbar_finish`
- L112 view_id_switch_case: `actionbar_back`
- L117 view_id_switch_case: `actionbar_finish`
- L140 view_id_switch_case: `contact_name_close`
- L143 view_id_switch_case: `contact_phone_close`
- L246 view_id_switch_case: `current_pass`
- L259 view_id_switch_case: `new_pass`

参照：L104 `registerListener` (onCreateView); L105 `registerListener` (onCreateView); L128 `addContact` (onClick); L133 `updateContact` (onClick); L160 `unRegisterListener` (onDestroyView); L161 `unRegisterListener` (onDestroyView)

## com/iermu/ui/fragment/camseting/cron/ChooseTimeFragment.java

- L95 OnClick_annotation: `set_all_day`, `set_only_day`, `set_only_night`, `set_custom_time`, `actionbar_back`
- L98 view_id_switch_case: `actionbar_back`
- L103 view_id_switch_case: `set_all_day`
- L112 view_id_switch_case: `set_only_day`
- L121 view_id_switch_case: `set_only_night`
- L130 view_id_switch_case: `set_custom_time`

## com/iermu/ui/fragment/camseting/cron/CronSettingFragment.java

- L258 OnClick_annotation: `set_time_quantum`, `set_repeat`, `actionbar_finish`, `actionbar_back`
- L261 view_id_switch_case: `actionbar_back`
- L265 view_id_switch_case: `actionbar_finish`
- L287 view_id_switch_case: `set_time_quantum`
- L292 view_id_switch_case: `set_repeat`

参照：L146 `registerListener` (onCreateView); L147 `registerListener` (onCreateView); L148 `registerListener` (onCreateView); L155 `syncCamAlarm` (onCreateView); L157 `syncCamPowerCron` (onCreateView); L159 `syncCamCvr` (onCreateView); L161 `getCamLive` (onCreateView); L174 `getCamLive` (refreshView); L179 `getAlarmCron` (refreshView); L182 `getCamCron` (refreshView); L186 `getCvrCron` (refreshView); L270 `setAlarmCronTime` (onClick); L277 `setCamCron` (onClick); L283 `setCvrCron` (onClick); L359 `unRegisterListener` (onDestroyView); L360 `unRegisterListener` (onDestroyView); L361 `unRegisterListener` (onDestroyView); L367 `isOffline` (onCamSetting)

## com/iermu/ui/fragment/camseting/cron/CustomTimeFragment.java

- L112 OnClick_annotation: `set_custom_start`, `set_custom_close`
- L115 view_id_switch_case: `set_custom_start`
- L126 view_id_switch_case: `set_custom_close`

参照：L124 `initData` (onClick); L135 `initData` (onClick)

## com/iermu/ui/fragment/camseting/cron/WeekCycleFragment.java

- L34 view_id_switch_case: `set_work_monday`
- L37 view_id_switch_case: `set_work_tuesday`
- L40 view_id_switch_case: `set_work_wednesday`
- L43 view_id_switch_case: `set_work_thursday`
- L46 view_id_switch_case: `set_work_friday`
- L49 view_id_switch_case: `set_work_saturday`
- L52 view_id_switch_case: `set_work_sunday`

## com/iermu/ui/fragment/camseting/lbs/CamLocationFragment.java

- L199 OnClick_annotation: `cam_search`, `location_again`, `select_no_view`, `refresh_view`
- L202 view_id_switch_case: `cam_search`
- L206 view_id_switch_case: `location_again`
- L207 view_id_switch_case: `refresh_view`
- L212 view_id_switch_case: `select_no_view`

参照：L141 `registerListener` (initView); L142 `registerListener` (initView); L149 `unRegisterListener` (onHiddenChanged); L151 `registerListener` (onHiddenChanged); L221 `dropLocation` (onClock); L231 `unRegisterListener` (onDestroyView); L232 `unRegisterListener` (onDestroyView); L244 `getLocation` (camLocation); L299 `getCount` (onRegeocodeSearched); L334 `getJsonLocation` (onItemClick); L335 `updateLocation` (onItemClick)

## com/iermu/ui/fragment/camseting/lbs/CamSearchLocationFragment.java

- L106 OnClick_annotation: `actionbar_back`, `actionbar_finish`
- L112 view_id_switch_case: `actionbar_back`
- L115 view_id_switch_case: `actionbar_finish`

参照：L102 `registerListener` (onCreateView); L133 `unRegisterListener` (onDestroyView); L171 `getContext` (onPoiSearched); L173 `getCount` (onPoiSearched); L318 `getJsonLocation` (onClick); L319 `updateLocation` (onClick)

## com/iermu/ui/fragment/camseting/local/LocalRecordSetting.java

- L273 OnClick_annotation: `actionbar_back`, `action_bar_btn_left`, `action_bar_btn_right`, `nas_set_again`, `nas_start_setting_btn`, `nas_mode_view`, `definition_view`, `record_mode_view`, `sd_definition_view`, `sd_card_format`
- L276 view_id_switch_case: `actionbar_back`
- L279 view_id_switch_case: `action_bar_btn_left`
- L290 view_id_switch_case: `action_bar_btn_right`
- L304 view_id_switch_case: `nas_set_again`
- L305 view_id_switch_case: `nas_start_setting_btn`
- L309 view_id_switch_case: `nas_mode_view`
- L310 view_id_switch_case: `record_mode_view`
- L319 view_id_switch_case: `definition_view`
- L320 view_id_switch_case: `sd_definition_view`
- L330 view_id_switch_case: `sd_card_format`
- L346 view_id_switch_case: `switch_button`

参照：L204 `getNasParam` (onHiddenChanged); L205 `getRecordType` (onHiddenChanged); L206 `getCodeGrade` (onHiddenChanged); L214 `getBaiduUid` (initView); L221 `getCamStorageInfo` (initView); L229 `registerListener` (initView); L230 `registerListener` (initView); L231 `registerListener` (initView); L232 `registerListener` (initView); L233 `registerListener` (initView); L234 `registerListener` (initView); L235 `registerListener` (initView); L236 `registerListener` (initView); L237 `registerListener` (initView); L238 `getNasParam` (initView); L239 `getRecordType` (initView); L248 `getCodeGrade` (initView); L299 `findCardAndNasInfo` (onClick); L300 `getRecordType` (onClick); L301 `getCodeGrade` (onClick); L360 `unRegisterListener` (onDestroyView); L361 `unRegisterListener` (onDestroyView); L362 `unRegisterListener` (onDestroyView); L363 `unRegisterListener` (onDestroyView); L364 `unRegisterListener` (onDestroyView); L365 `unRegisterListener` (onDestroyView); L366 `unRegisterListener` (onDestroyView); L367 `unRegisterListener` (onDestroyView); L368 `unRegisterListener` (onDestroyView); L410 `kernelUpgrade` (onClick); L427 `setNasParam` (setSwitchButton); L561 `restartCamDev` (OnSetNasParamListener); L605 `FormatSdc` (cardFormat); L681 `setRecordType` (setRecordMode); L692 `setCodeGrade` (setDefinition); L693 `getCodeGrade` (setDefinition)

## com/iermu/ui/fragment/camseting/local/NasDataFragment.java

- L181 OnClick_annotation: `nas_set_again`, `definition_view`, `nas_mode_view`, `definition_view`, `nas_start_setting_btn`
- L184 view_id_switch_case: `nas_set_again`
- L185 view_id_switch_case: `nas_start_setting_btn`
- L189 view_id_switch_case: `nas_mode_view`
- L199 view_id_switch_case: `definition_view`
- L228 view_id_switch_case: `switch_button`

参照：L139 `getNasParam` (onHiddenChanged); L147 `getBaiduUid` (initView); L149 `getCamStorageInfo` (initView); L162 `registerListener` (initView); L163 `registerListener` (initView); L164 `registerListener` (initView); L165 `registerListener` (initView); L166 `registerListener` (initView); L167 `registerListener` (initView); L168 `registerListener` (initView); L169 `getNasParam` (initView); L170 `getRecordType` (initView); L176 `getCodeGrade` (initView); L215 `unRegisterListener` (onDestroyView); L216 `unRegisterListener` (onDestroyView); L217 `unRegisterListener` (onDestroyView); L218 `unRegisterListener` (onDestroyView); L219 `unRegisterListener` (onDestroyView); L220 `unRegisterListener` (onDestroyView); L221 `unRegisterListener` (onDestroyView); L350 `kernelUpgrade` (onClick); L367 `setNasParam` (setSwitchButton); L382 `restartCamDev` (OnSetNasParamListener); L441 `setRecordType` (setRecordMode); L452 `setCodeGrade` (setDefinition)

## com/iermu/ui/fragment/camseting/local/NasSettingFragment.java

- L196 OnClick_annotation: `actionbar_back`, `btn_scan`, `actionbar_finish`, `nas_dir`
- L208 view_id_switch_case: `actionbar_back`
- L211 view_id_switch_case: `actionbar_finish`
- L267 view_id_switch_case: `nas_dir`

参照：L142 `getBaiduUid` (initView); L146 `getNasParam` (initView); L147 `registerListener` (initView); L148 `registerListener` (initView); L149 `registerListener` (initView); L150 `registerListener` (initView); L151 `registerListener` (initView); L165 `unRegisterListener` (onDestroy); L166 `unRegisterListener` (onDestroy); L167 `unRegisterListener` (onDestroy); L168 `unRegisterListener` (onDestroy); L169 `unRegisterListener` (onDestroy); L243 `kernelUpgrade` (onClick); L260 `setNasParam` (onClick); L379 `restartCamDev` (OnSetNasParamListener); L380 `getCamStorageInfo` (OnSetNasParamListener); L401 `getContext` (OnKernelUpgradeListener); L410 `setNasParam` (OnKernelUpgradeListener)

## com/iermu/ui/fragment/camseting/local/ScanNasDirFragment.java

- L118 OnClick_annotation: `scan_dir`
- L121 view_id_switch_case: `scan_dir`

参照：L113 `registerListener` (initView); L114 `getBaiduUid` (initView); L136 `unRegisterListener` (onDestroyView); L182 `getNasPath` (getDirList)

## com/iermu/ui/fragment/camseting/local/SdSettingFragment.java

- L191 OnClick_annotation: `sd_card_format`, `record_mode_view`, `sd_definition_view`
- L194 view_id_switch_case: `sd_card_format`
- L203 view_id_switch_case: `record_mode_view`
- L212 view_id_switch_case: `sd_definition_view`

参照：L120 `getCamStorageInfo` (onCreateView); L141 `registerListener` (onCreateView); L142 `registerListener` (onCreateView); L143 `registerListener` (onCreateView); L144 `registerListener` (onCreateView); L145 `registerListener` (onCreateView); L146 `registerListener` (onCreateView); L147 `findCardAndNasInfo` (onCreateView); L148 `getRecordType` (onCreateView); L154 `getCodeGrade` (onCreateView); L228 `unRegisterListener` (onDestroyView); L229 `unRegisterListener` (onDestroyView); L230 `unRegisterListener` (onDestroyView); L231 `unRegisterListener` (onDestroyView); L232 `unRegisterListener` (onDestroyView); L233 `unRegisterListener` (onDestroyView); L346 `FormatSdc` (cardFormat); L381 `setRecordType` (setRecordMode); L392 `setCodeGrade` (setDefinition)

## com/iermu/ui/fragment/dev433/Dev433DetailFragment.java

- L113 OnClick_annotation: `add_sure`, `add_know`
- L116 view_id_switch_case: `actionbar_back`
- L121 view_id_switch_case: `add_sure`
- L133 view_id_switch_case: `add_know`

参照：L102 `registerListener` (onCreateView); L110 `unRegisterListener` (onDestroyView); L130 `add433alarm` (onClick); L175 `getUid` (onCheck433Name); L176 `getUid` (onCheck433Name); L176 `getUid` (onCheck433Name); L177 `getUid` (onCheck433Name); L177 `getUid` (onCheck433Name); L193 `getCount` (onCheck433Name); L197 `getCount` (onCheck433Name); L215 `set433DevTestStatus` (onAdd433alarm)

## com/iermu/ui/fragment/dev433/Dev433EditNameFragment.java

- L125 OnClick_annotation: `actionbar_finish`, `actionbar_back`, `dev_name_close`
- L129 view_id_switch_case: `actionbar_back`
- L132 view_id_switch_case: `actionbar_finish`
- L141 view_id_switch_case: `dev_name_close`

参照：L104 `registerListener` (onCreateView); L122 `unRegisterListener` (onDestroyView); L134 `update433DevName` (onClick)

## com/iermu/ui/fragment/dev433/Dev433SettingFragment.java

- L86 OnClick_annotation: `set_name`, `set_test`, `set_delete_dev`
- L89 view_id_switch_case: `set_name`
- L93 view_id_switch_case: `set_test`
- L97 view_id_switch_case: `set_delete_dev`

参照：L58 `registerListener` (onCreateView); L75 `get433DevTestStatus` (initView); L83 `unRegisterListener` (onDestroyView); L101 `delete433Dev` (onClick)

## com/iermu/ui/fragment/dev433/Dev433TestAlarmFragment.java

- L168 OnClick_annotation: `test_433_trigger_sure`
- L171 view_id_switch_case: `test_433_trigger_sure`

参照：L97 `getCamLive` (onCreateView); L153 `handleMessage` (handleMessage)

## com/iermu/ui/fragment/dev433/Dev433TestOpenFragment.java

- L129 OnClick_annotation: `test_433_fail_know`, `tell_number`
- L132 view_id_switch_case: `tell_number`
- L139 view_id_switch_case: `test_433_fail_know`

参照：L97 `registerListener` (onCreateView); L98 `registerListener` (onCreateView); L113 `setAlarmPush` (initView); L125 `unRegisterListener` (onPause); L126 `unRegisterListener` (onPause)

## com/iermu/ui/fragment/dev433/Dev433TestReceiveFragment.java

- L171 OnClick_annotation: `test_433_fail_know`
- L174 view_id_switch_case: `test_433_fail_know`

参照：L133 `initDate` (initView); L138 `handleMessage` (handleMessage)

## com/iermu/ui/fragment/dev433/Dev433TestTriggerFragment.java

- L215 OnClick_annotation: `test_433_trigger_sure`
- L218 view_id_switch_case: `test_433_trigger_sure`

参照：L125 `clear433Alarm` (onCreateView)

## com/iermu/ui/fragment/live/BiliDanmukuParser.java


参照：L229 `newInstance` (characters)

## com/iermu/ui/fragment/live/DanmakuManger.java


参照：L179 `newInstance` (createInputstream); L208 `newInstance` (createInputstream)

## com/iermu/ui/fragment/live/DeviceCategoryFragment.java

- L76 OnClick_annotation: `category_help`
- L79 view_id_switch_case: `category_help`

## com/iermu/ui/fragment/live/DeviceSearchFragment.java

- L214 OnClick_annotation: `actionbar_back`, `actionbar_finish`, `clear_text_img`, `mine_cam_search`, `public_cam_search`, `more_mine_cam`, `more_public_cam`
- L217 view_id_switch_case: `actionbar_back`
- L261 view_id_switch_case: `actionbar_finish`
- L284 view_id_switch_case: `clear_text_img`
- L288 view_id_switch_case: `more_mine_cam`
- L300 view_id_switch_case: `more_public_cam`
- L312 view_id_switch_case: `mine_cam_search`
- L318 view_id_switch_case: `public_cam_search`

参照：L186 `registerListener` (onCreateView); L187 `registerListener` (onCreateView); L204 `unRegisterListener` (onDestroyView); L205 `unRegisterListener` (onDestroyView); L273 `searchDevices` (onClick); L276 `searchDevices` (onClick); L279 `searchDevices` (onClick); L280 `searchDevices` (onClick); L463 `searchDevices` (onEditorAction); L467 `searchDevices` (onEditorAction); L470 `searchDevices` (onEditorAction); L471 `searchDevices` (onEditorAction)

## com/iermu/ui/fragment/live/FourLiveFragment.java


参照：L114 `getMineCamList` (getItemList); L115 `getAuthorizeCamList` (getItemList); L119 `getCategoryList` (getItemList)

## com/iermu/ui/fragment/live/FourLiveMainFragment.java

- L120 OnClick_annotation: `four_live_back`
- L123 view_id_switch_case: `four_live_back`

参照：L93 `registerListener` (onCreateView); L96 `registerListener` (onCreateView); L98 `getMineCamList` (onCreateView); L100 `getCategoryList` (onCreateView); L107 `isInFourLiveFirst` (onCreateView); L108 `setIsInFourLiveFirst` (onCreateView); L130 `getMineCamList` (getPageList); L159 `getMineCamList` (onMimeCamChanged); L169 `unRegisterListener` (onDestroy)

## com/iermu/ui/fragment/live/MainRecordFragment.java


参照：L178 `registerListener` (onCreateView); L179 `getCardRecPicker` (onCreateView); L181 `registerListener` (onCreateView); L182 `getStorage` (onCreateView); L183 `getCamLive` (onCreateView); L185 `getCardInfo` (onCreateView); L281 `getCamLive` (checkIsShowLocalRec); L282 `isHaveLocalRec` (checkIsShowLocalRec); L299 `unRegisterListener` (onDestroyView); L300 `unRegisterListener` (onDestroyView); L404 `getCamStorageInfo` (onGetStorage); L416 `setIsHaveLocalRec` (onGetStorage)

## com/iermu/ui/fragment/live/MineCardRecordFragment.java

- L710 OnClick_annotation: `rec_video_play_btn`, `rec_capture_ll`, `rec_sound_switch_ll`, `live_mine_close`, `viewTime`, `textViewCloseTimeList`, `rec_select_date_ll`, `rec_card_info_ll`, `rec_change_to_live_btn`, `card_video_cut`
- L713 view_id_switch_case: `live_mine_close`
- L716 view_id_switch_case: `textViewCloseTimeList`
- L719 view_id_switch_case: `rec_sound_switch_ll`
- L723 view_id_switch_case: `rec_capture_ll`
- L727 view_id_switch_case: `rec_select_date_ll`
- L739 view_id_switch_case: `rec_video_play_btn`
- L742 view_id_switch_case: `rec_change_to_live_btn`
- L752 view_id_switch_case: `card_video_cut`
- L766 view_id_switch_case: `rec_card_info_ll`

参照：L338 `handleMessage` (handleMessage); L427 `getDayTimeList` (scrollStop); L469 `getCamLive` (onCreateView); L492 `registerListener` (onCreateView); L493 `registerListener` (onCreateView); L494 `registerListener` (onCreateView); L495 `registerListener` (onCreateView); L496 `registerListener` (onCreateView); L497 `registerListener` (onCreateView); L498 `clearCamStorageInfo` (onCreateView); L499 `getStorage` (onCreateView); L500 `getCardRecPicker` (onCreateView); L501 `getCardRecordList` (onCreateView); L502 `getCardInfo` (onCreateView); L506 `registerListener` (onCreateView); L507 `registerListener` (onCreateView); L509 `getSoundStatus` (onCreateView); L514 `getDayTimeList` (onCreateView); L743 `closeCardRecord` (onClick); L753 `isConnected` (onClick); L767 `getCardInfo` (onClick); L768 `getCamLive` (onClick); L851 `getDevIsRemote` (onFragmentResume); L852 `instance` (onFragmentResume); L852 `hasRemoteRTMP2MP4` (onFragmentResume); L854 `instance` (onFragmentResume); L854 `hasDownloadRTMP2MP4` (onFragmentResume); L872 `getDevIsRemote` (onFragmentPause); L873 `instance` (onFragmentPause); L873 `hasRemoteRTMP2MP4` (onFragmentPause); L875 `instance` (onFragmentPause); L875 `hasDownloadRTMP2MP4` (onFragmentPause); L878 `closeCardRecord` (onFragmentPause); L884 `closeCardRecord` (onFragmentDestroy); L893 `unRegisterListener` (onDestroyView); L894 `getDevIsRemote` (onDestroyView); L895 `instance` (onDestroyView); L895 `stopRemoteRTMP2MP4` (onDestroyView); L897 `instance` (onDestroyView); L897 `stopDownloadRTMP2MP4` (onDestroyView); L917 `clearCardInfo` (onDestroyView); L921 `unRegisterListener` (onDestroyView); L922 `unRegisterListener` (onDestroyView); L923 `unRegisterListener` (onDestroyView); L924 `unRegisterListener` (onDestroyView); L925 `unRegisterListener` (onDestroyView); L926 `unRegisterListener` (onDestroyView); L927 `unRegisterListener` (onDestroyView); L932 `closeCardRecord` (onDestroyView); L938 `stopLoadData` (onDestroyView); L1111 `getDayTimeList` (onRecordDayChanged); L1113 `getDayTimeList` (onRecordDayChanged); L1119 `getCardRecordList` (onRecordDayChanged); L1134 `getDayTimeList` (onRecordDayChanged); L1165 `getCardRecPicker` (onRefreshClick); L1176 `getCardInfo` (onRefreshClick); L1177 `getCardRecPicker` (onRefreshClick); L1215 `getCardRecordList` (playFinish); L1233 `getCardRecPicker` (onError); L1291 `cardVodSeek` (onPrepared); L1303 `isConnected` (onOpenPlayCardRecord); L1341 `getDevIsRemote` (onOpenPlayCardRecord); L1342 `instance` (onOpenPlayCardRecord); L1342 `hasRemoteRTMP2MP4` (onOpenPlayCardRecord); L1344 `instance` (onOpenPlayCardRecord); L1344 `hasDownloadRTMP2MP4` (onOpenPlayCardRecord); L1387 `getDevIsRemote` (playRecord); L1388 `instance` (playRecord); L1388 `hasRemoteRTMP2MP4` (playRecord); L1390 `instance` (playRecord); L1390 `hasDownloadRTMP2MP4` (playRecord); L1400 `getCardRecordList` (playRecord); L1427 `getDayTimeList` (playRecord); L1452 `openCardPlayRecord` (playRecord); L1471 `getDevIsRemote` (setEnable); L1472 `instance` (setEnable); L1472 `hasRemoteRTMP2MP4` (setEnable); L1474 `instance` (setEnable); L1474 `hasDownloadRTMP2MP4` (setEnable); L1539 `setSoundStatus` (onSoundStateChange); L1601 `getDayTimeList` (changeDay); L1630 `getCardInfo` (drawTimeLine); L1639 `getCardRecordList` (drawTimeLine); L1652 `getCardInfo` (OnCardRecordShow); L1658 `getDevIsRemote` (OnCardRecordShow); L1662 `getCardRecPicker` (OnCardRecordShow); L1673 `getCardRecPicker` (OnCardRecordShow); L1685 `getCardRecPicker` (OnCardRecordShow); L1691 `getCardRecPicker` (OnCardRecordShow); L1713 `init` (OnCardRecordShow); L1713 `getDayTimeList` (OnCardRecordShow); L1714 `getCardRecPicker` (OnCardRecordShow); L1718 `getCardRecPicker` (OnCardRecordShow); L1766 `getStorage` (executeTask); L1770 `isConnected` (executeTask); L1863 `getStorage` (executeTask); L1948 `getCardInfo` (onClick); L1967 `getCamStorageInfo` (showDevUseModeStatus); L2032 `getLiveMedia` (onGetRemoteRecordDownLoadUrl); L2032 `getLiveMedia` (onGetRemoteRecordDownLoadUrl); L2044 `getLanCamUid` (onGetRemoteRecordDownLoadUrl); L2045 `instance` (onGetRemoteRecordDownLoadUrl); L2045 `setOnRTMP2MP4Listener` (onGetRemoteRecordDownLoadUrl); L2046 `instance` (onGetRemoteRecordDownLoadUrl); L2046 `startRemoteRTMP2MP4` (onGetRemoteRecordDownLoadUrl); L2046 `getLiveMedia` (onGetRemoteRecordDownLoadUrl); L2069 `getCardRecordList` (run); L2074 `closeCardRecord` (run); L2093 `keepRemotePlayStatus` (run); L2098 `getRecordType` (dialogCardInfo); L2099 `getCodeGrade` (dialogCardInfo); L2138 `getCardRecordList` (downloadRtmp2mp4); L2219 `getDevIsRemote` (downloadRtmp2mp4); L2220 `getRemoteRecordDownLoadUrl` (downloadRtmp2mp4); L2223 `closeCardRecord` (downloadRtmp2mp4); L2237 `getLanCamUid` (downloadRtmp2mp4); L2238 `instance` (downloadRtmp2mp4); L2238 `setOnRTMP2MP4Listener` (downloadRtmp2mp4); L2239 `instance` (downloadRtmp2mp4); L2239 `startDownloadRTMP2MP4` (downloadRtmp2mp4); L2297 `isConnected` (onRtmp2MP4StatusChanged); L2303 `getDevIsRemote` (onRtmp2MP4StatusChanged); L2304 `instance` (onRtmp2MP4StatusChanged); L2304 `stopRemoteRTMP2MP4` (onRtmp2MP4StatusChanged); L2307 `instance` (onRtmp2MP4StatusChanged); L2307 `stopDownloadRTMP2MP4` (onRtmp2MP4StatusChanged); L2345 `isConnected` (onRtmp2MP4Error); L2395 `isConnected` (onClick); L2415 `isConnected` (onClick); L2429 `isConnected` (onClick); L2511 `getDevIsRemote` (onCancelDownloading); L2512 `instance` (onCancelDownloading); L2512 `stopRemoteRTMP2MP4` (onCancelDownloading); L2514 `instance` (onCancelDownloading); L2514 `stopDownloadRTMP2MP4` (onCancelDownloading); L2534 `getDevIsRemote` (onClick); L2535 `instance` (onClick); L2535 `stopRemoteRTMP2MP4` (onClick); L2537 `instance` (onClick); L2537 `stopDownloadRTMP2MP4` (onClick)

## com/iermu/ui/fragment/live/MineLiveFragment.java

- L916 OnClick_annotation: `view_cloud`, `buttonLevel`, `buttonSound`, `live_jump_rec_rl`, `imageButtonSpeak`, `imageButtonPrintScreen`, `screen_cap_btn`, `viewAddPrePositionBg`, `buttonPopCancel`, `buttonPopDelete`, `buttonPopEdit`, `actionbar_back`, `guid_double_click_view`, `guid_on_long_press`, `guid_cloud_auto`
- L919 view_id_switch_case: `actionbar_back`
- L922 view_id_switch_case: `view_cloud`
- L969 view_id_switch_case: `live_jump_rec_rl`
- L996 view_id_switch_case: `buttonLevel`
- L999 view_id_switch_case: `buttonSound`
- L1004 view_id_switch_case: `screen_cap_btn`
- L1024 view_id_switch_case: `imageButtonPrintScreen`
- L1034 view_id_switch_case: `viewAddPrePositionBg`
- L1040 view_id_switch_case: `buttonPopEdit`
- L1049 view_id_switch_case: `buttonPopDelete`
- L1068 view_id_switch_case: `buttonPopCancel`

参照：L288 `handleMessage` (handleMessage); L295 `startLyyAudio` (handleMessage); L298 `getBaiduAccessToken` (handleMessage); L299 `startBaiduAudio` (handleMessage); L309 `stopAudio` (handleMessage); L333 `handleMessage` (handleMessage); L380 `outSideCloudMoveFirst` (onDoubleTap); L381 `outSideCloudMoveFirst` (onDoubleTap); L383 `outSideCloudMove` (onDoubleTap); L384 `outSideCloudMove` (onDoubleTap); L393 `cloudMove` (onDoubleTap); L475 `getCamLive` (onCreateView); L476 `getUid` (onCreateView); L478 `registerListener` (onCreateView); L479 `getCardInfo` (onCreateView); L489 `getCardRecPicker` (onCreateView); L554 `getSoundStatus` (onCreateView); L560 `getCamStatus` (onCreateView); L562 `syncCamStatus` (onCreateView); L563 `registerListener` (onCreateView); L567 `registerListener` (onCreateView); L568 `registerListener` (onCreateView); L573 `getCapsule` (run); L576 `registerListener` (onCreateView); L578 `getCamLive` (onCreateView); L583 `checkIsRotate` (onCreateView); L584 `registerListener` (onCreateView); L585 `registerListener` (onCreateView); L586 `registerListener` (onCreateView); L587 `registerListener` (onCreateView); L588 `registerListener` (onCreateView); L589 `registerListener` (onCreateView); L590 `registerListener` (onCreateView); L591 `registerListener` (onCreateView); L594 `registerListener` (onCreateView); L599 `openLiveMedia` (onCreateView); L601 `addLiveNum` (onCreateView); L602 `registerListener` (onCreateView); L611 `getCardRecPicker` (onCreateView); L619 `getCardRecPicker` (onCreateView); L647 `getCardInfo` (OnClickCardFormat); L834 `openLiveMedia` (onResume); L853 `closeLiveMedia` (onPause); L867 `unRegisterListener` (onDestroyView); L868 `clearCardInfo` (onDestroyView); L890 `unRegisterListener` (onDestroyView); L891 `unRegisterListener` (onDestroyView); L892 `unRegisterListener` (onDestroyView); L893 `unRegisterListener` (onDestroyView); L894 `unRegisterListener` (onDestroyView); L895 `unRegisterListener` (onDestroyView); L896 `unRegisterListener` (onDestroyView); L897 `unRegisterListener` (onDestroyView); L898 `unRegisterListener` (onDestroyView); L899 `unRegisterListener` (onDestroyView); L900 `unRegisterListener` (onDestroyView); L901 `unRegisterListener` (onDestroyView); L902 `unRegisterListener` (onDestroyView); L903 `closeLiveMedia` (onDestroyView); L912 `unRegisterListener` (onDestroyView); L925 `getLiveGuidFirst` (onClick); L945 `getListPreset` (onClick); L961 `setLiveGuidFirst` (onClick); L1011 `stopRecord` (onClick); L1015 `stopRecord` (onClick); L1053 `dropPreset` (onClick); L1159 `getCamLive` (onLiveMediaChanged); L1168 `getLiveMedia` (onLiveMediaChanged); L1171 `isConnected` (onLiveMediaChanged); L1185 `statStartPlay` (onLiveMediaChanged); L1197 `isOffline` (onLiveMediaChanged); L1208 `isConnected` (onLiveMediaChanged); L1245 `isConnected` (onError); L1247 `isOffline` (onError); L1261 `openLiveMedia` (onError); L1336 `setSoundStatus` (onAudioClosed); L1362 `setSoundStatus` (onAudioError); L1414 `stopRecord` (onRecordSizeChanged); L1426 `stopRecord` (onRecordSizeChanged); L1532 `setBitLevel` (onLevelClick); L1545 `setBitLevel` (onLevelClick); L1558 `setBitLevel` (onLevelClick); L1578 `stopRecord` (onScreenCapClick); L1582 `stopRecord` (onScreenCapClick); L1705 `openLiveMedia` (onRefreshClick); L1728 `powerCamDev` (onButtonStartClick); L1753 `startCloudRotate` (onStartAutoClick); L1761 `stopCloudRotate` (onStopAutoClick); L1802 `checkIsRotate` (onCheckIsRotate); L1825 `getCamPreset` (onUpdatePresetTitle); L1858 `getCamPreset` (onListPreset); L1883 `startCloudRotate` (onButtonAutoClick); L1889 `stopCloudRotate` (onButtonAutoClick); L1901 `getContext` (onButtonCloudClick); L1915 `getListPreset` (onButtonCloudClick); L1980 `cloudMovePreset` (run); L1987 `getListPreset` (onPreRefreshClick); L1994 `getListPreset` (onPreRefreshClickCtrl); L2062 `addPreset` (onSubmitClick); L2067 `updatePresetTitle` (onSubmitClick); L2081 `openLiveMedia` (run); L2117 `getCamStatus` (onCamSetting); L2151 `setSoundStatus` (onSoundStateChange); L2171 `isOffline` (refreshStatusView); L2465 `startRecord` (setScreenCapStart); L2510 `getListPreset` (run)

## com/iermu/ui/fragment/live/MineRecordFragment.java

- L893 OnClick_annotation: `rec_video_play_btn`, `rec_capture_ll`, `rec_sound_switch_ll`, `live_mine_close`, `rec_select_date_ll`, `textViewCloseTimeList`, `rec_setting_ll`, `time_line_mode_btn`, `thumbnail_mode_btn`, `viewFilmEdit`, `viewLyyFilmEdit`, `buttonDetail`, `rec_change_to_live_btn`, `i_know_remind_btn`, `rec_download_remind_layout`
- L896 view_id_switch_case: `live_mine_close`
- L899 view_id_switch_case: `buttonDetail`
- L902 view_id_switch_case: `textViewCloseTimeList`
- L905 view_id_switch_case: `rec_setting_ll`
- L915 view_id_switch_case: `rec_sound_switch_ll`
- L919 view_id_switch_case: `rec_capture_ll`
- L923 view_id_switch_case: `rec_select_date_ll`
- L935 view_id_switch_case: `rec_video_play_btn`
- L938 view_id_switch_case: `rec_change_to_live_btn`
- L942 view_id_switch_case: `time_line_mode_btn`
- L946 view_id_switch_case: `viewFilmEdit`
- L957 view_id_switch_case: `viewLyyFilmEdit`
- L968 view_id_switch_case: `rec_download_remind_layout`
- L969 view_id_switch_case: `i_know_remind_btn`
- L972 view_id_switch_case: `thumbnail_mode_btn`

参照：L345 `handleMessage` (handleMessage); L428 `getDayTimeList` (scrollStop); L479 `getCamLive` (onCreateView); L530 `registerListener` (onCreateView); L531 `registerListener` (onCreateView); L533 `getSoundStatus` (onCreateView); L539 `getCamLive` (onCreateView); L547 `registerListener` (onCreateView); L548 `registerListener` (onCreateView); L549 `registerListener` (onCreateView); L550 `initData` (onCreateView); L551 `getCardInfo` (onCreateView); L552 `registerListener` (onCreateView); L553 `registerListener` (onCreateView); L554 `registerListener` (onCreateView); L556 `init` (onCreateView); L556 `getDayTimeList` (onCreateView); L558 `getBdyFilmEditIsSuccess` (onCreateView); L561 `initDate` (onCreateView); L563 `getBdyIsClipping` (onCreateView); L579 `getLyyFilmEditIsSuccess` (onCreateView); L582 `initDate` (onCreateView); L584 `getLyyIsClipping` (onCreateView); L584 `getLyyDownloadStatus` (onCreateView); L610 `getDayTimeList` (onCreateView); L625 `isConnected` (onCreateView); L626 `findRecordList` (onCreateView); L627 `findThumbnailList` (onCreateView); L628 `getCardRecPicker` (onCreateView); L704 `isConnected` (onCreateView); L1016 `unRegisterListener` (onFragmentPause); L1017 `closeLiveMedia` (onFragmentPause); L1033 `closeRecord` (onFragmentDestroy); L1042 `unRegisterListener` (onDestroyView); L1052 `clearCardInfo` (onDestroyView); L1055 `unRegisterListener` (onDestroyView); L1056 `unRegisterListener` (onDestroyView); L1057 `unRegisterListener` (onDestroyView); L1058 `unRegisterListener` (onDestroyView); L1059 `unRegisterListener` (onDestroyView); L1060 `unRegisterListener` (onDestroyView); L1067 `closeRecord` (onDestroyView); L1076 `stopLoadData` (onDestroyView); L1259 `getCamLive` (onRecordChanged); L1265 `getDayTimeList` (onRecordChanged); L1267 `getDayTimeList` (onRecordChanged); L1269 `getRecordList` (onRecordChanged); L1327 `getDayTimeList` (onRecordChanged); L1353 `getThumbnailList` (onThumbnailChange); L1362 `isConnected` (onRefreshClick); L1377 `findRecordList` (onRefreshClick); L1378 `findThumbnailList` (onRefreshClick); L1379 `getCardRecPicker` (onRefreshClick); L1408 `getRecordList` (onCompletion); L1422 `getDayTimeList` (onCompletion); L1604 `getRecordList` (playRecord); L1639 `openPlayRecord` (playRecord); L1643 `vodSeek` (playRecord); L1673 `getDayTimeList` (selectThumbnail); L1707 `setSoundStatus` (onSoundStateChange); L1763 `getDayTimeList` (changeDay); L1789 `getThumbnailList` (setThumbnailSelection); L1853 `getRecordList` (drawTimeLine); L1952 `getCardInfo` (OnClickCardFormat); L2087 `setLyyClippingStatus` (OnLyyDownloadNumListener); L2088 `setLyyDownloadStatus` (OnLyyDownloadNumListener); L2089 `saveLyyFilmEditIsSuccess` (OnLyyDownloadNumListener); L2110 `getCount` (run); L2236 `getRecordList` (onClick); L2252 `saveLyyFilmEditTime` (onClick); L2253 `getLyyFilmEditStartTime` (onClick); L2253 `getLyyFilmEditEndTime` (onClick); L2256 `startLyyClipRec` (onClick); L2280 `saveBdyFilmEditTime` (onClick); L2281 `startBdyClipRec` (onClick); L2314 `saveLyyFilmEditIsSuccess` (showFilmEditFailedDialog); L2338 `getLyyFilmEditStartTime` (onClick); L2339 `getLyyFilmEditEndTime` (onClick); L2341 `startLyyClipRec` (onClick); L2345 `lyyRetryDownloadVideo` (onClick); L2365 `saveLyyFilmEditIsSuccess` (onClick); L2372 `saveBdyFilmEditIsSuccess` (showFilmEditFailedDialog); L2392 `getBdyFilmEditStartTime` (onClick); L2393 `getBdyFilmEditEndTime` (onClick); L2395 `startBdyClipRec` (onClick); L2413 `saveBdyFilmEditIsSuccess` (onClick)

## com/iermu/ui/fragment/live/PublicCamFragment.java

- L124 OnClick_annotation: `error_btn`
- L127 view_id_switch_case: `error_btn`

参照：L109 `registerListener` (onCreateView); L110 `syncNewCamList` (onCreateView); L112 `getCount` (onCreateView); L113 `getCount` (onCreateView); L130 `syncNewCamList` (onClick); L140 `unRegisterListener` (onDestroyView); L156 `getNextPageNum` (onScrollStateChanged); L158 `syncOldCamList` (onScrollStateChanged); L176 `getCount` (updateData); L177 `syncNewCamList` (updateData); L184 `syncNewCamList` (onRefresh); L226 `getCamList` (onPublicCamChanged); L228 `getCount` (onPublicCamChanged)

## com/iermu/ui/fragment/live/PublicLiveFragment.java

- L363 view_id_switch_case: `share_link`
- L369 view_id_switch_case: `share_code`
- L375 view_id_switch_case: `textViewDanmu`
- L379 view_id_switch_case: `textViewSound`
- L383 view_id_switch_case: `textViewShare`
- L394 view_id_switch_case: `textViewStore`
- L451 view_id_switch_case: `buttonSend`
- L454 view_id_switch_case: `share_wechat`
- L471 view_id_switch_case: `share_moments`
- L488 view_id_switch_case: `share_cancel`

参照：L206 `handleMessage` (handleMessage); L249 `getCamLive` (onCreateView); L251 `getCamLive` (onCreateView); L302 `registerListener` (onCreateView); L303 `registerListener` (onCreateView); L304 `registerListener` (onCreateView); L305 `registerListener` (onCreateView); L307 `registerListener` (onCreateView); L308 `syncNewCommentList` (onCreateView); L327 `findCamLive` (onCreateView); L328 `getUid` (onCreateView); L340 `viewCam` (onCreateView); L341 `registerListener` (onCreateView); L364 `getPubShareLiveLink` (onClick); L371 `getPubShareLiveLink` (onClick); L402 `getBaiduUid` (onClick); L404 `isLogin` (onClick); L416 `store` (onClick); L433 `store` (onClick); L467 `getCamLive` (onClick); L484 `getCamLive` (onClick); L549 `refreshCommentList` (run); L561 `closeLiveMedia` (onPause); L593 `unRegisterListener` (onDestroyView); L594 `unRegisterListener` (onDestroyView); L595 `unRegisterListener` (onDestroyView); L596 `unRegisterListener` (onDestroyView); L598 `closeLiveMedia` (onDestroyView); L599 `unRegisterListener` (onDestroyView); L645 `isLogin` (onReportClick); L690 `favour` (onFavourClick); L699 `findCamLive` (onRefreshClick); L702 `openPubLiveMedia` (onRefreshClick); L704 `openPubLiveMediaWithPassword` (onRefreshClick); L729 `getCommentList` (onCamCommentChanged); L768 `syncOldCommentList` (onLoadMore); L782 `isOffline` (onError); L805 `isOffline` (onLiveMediaChanged); L830 `findCamLive` (run); L837 `getLiveMedia` (onLiveMediaChanged); L848 `isOffline` (onLiveMediaChanged); L861 `statStartPlay` (onLiveMediaChanged); L864 `isConnected` (onLiveMediaChanged); L889 `getCommentList` (onPrepared); L924 `openPubLiveMedia` (refreshView); L926 `openPubLiveMediaWithPassword` (refreshView); L933 `isLogin` (refreshView); L973 `store` (store); L977 `unStore` (storeCancel); L1008 `isLogin` (send); L1015 `sendComment` (send); L1031 `getCount` (refreshAdapter); L1032 `getCommentList` (refreshAdapter); L1048 `isOffline` (refreshStatusView)

## com/iermu/ui/fragment/message/AlarmBigImageFragment.java

- L186 OnClick_annotation: `action_back`, `save_or_delete`, `to_live_lyout`, `to_hist_lyout`, `contact_call`
- L102 view_id_switch_case: `save_picture`
- L109 view_id_switch_case: `delete_picture`
- L189 view_id_switch_case: `to_live_lyout`
- L207 view_id_switch_case: `contact_call`
- L218 view_id_switch_case: `to_hist_lyout`
- L233 view_id_switch_case: `action_back`
- L236 view_id_switch_case: `save_or_delete`

参照：L147 `registerListener` (onCreateView); L149 `getCamLive` (onCreateView); L150 `initData` (onCreateView); L151 `registerListener` (onCreateView); L152 `getAlarmDatas` (onCreateView); L166 `getCount` (onCreateView); L172 `findAlarmDataRecord` (onCreateView); L182 `unRegisterListener` (onDestroyView); L183 `unRegisterListener` (onDestroyView); L255 `getCount` (onPageChanged); L270 `findAlarmDataRecord` (onPageSelected); L272 `getCount` (onPageSelected); L286 `getCount` (onPageScrollStateChanged); L296 `getAlarmDatas` (onDelAlarmData); L393 `delAlarmData` (onClick)

## com/iermu/ui/fragment/message/AlarmMessageFragment.java

- L170 OnClick_annotation: `del_btn`, `select_all`, `select_delete`, `undel_btn`, `back`, `alarm_btn`, `alarm_all_btn`, `alarm_move_btn`, `alarm_audio_btn`, `alarm_pir_btn`, `alarm_sos_btn`, `alarm_door_btn`, `alarm_smoke_btn`
- L173 view_id_switch_case: `back`
- L176 view_id_switch_case: `alarm_btn`
- L179 view_id_switch_case: `del_btn`
- L183 view_id_switch_case: `undel_btn`
- L190 view_id_switch_case: `select_all`
- L197 view_id_switch_case: `select_delete`
- L215 view_id_switch_case: `alarm_all_btn`
- L220 view_id_switch_case: `alarm_move_btn`
- L225 view_id_switch_case: `alarm_audio_btn`
- L230 view_id_switch_case: `alarm_sos_btn`
- L235 view_id_switch_case: `alarm_pir_btn`
- L240 view_id_switch_case: `alarm_door_btn`
- L245 view_id_switch_case: `alarm_smoke_btn`

参照：L152 `getAlarmDatas` (onCreateView); L159 `registerListener` (onCreateView); L160 `registerListener` (onCreateView); L161 `registerListener` (onCreateView); L162 `markReadAlarmNotice` (onCreateView); L163 `syncNewAlarmData` (onCreateView); L165 `getAlarmSpace` (onCreateView); L256 `unRegisterListener` (onDestroyView); L257 `unRegisterListener` (onDestroyView); L258 `unRegisterListener` (onDestroyView); L264 `unRegisterListener` (onHiddenChanged); L267 `registerListener` (onHiddenChanged); L268 `getAlarmDatas` (onHiddenChanged); L293 `syncNextAlarmData` (onScrollStateChanged); L314 `getAlarmDatas` (onAlarmDataChange); L353 `getAlarmDatas` (onDelAlarmData); L366 `syncNewAlarmData` (onDelAlarmData); L368 `syncNextAlarmData` (onDelAlarmData); L380 `delAlarmData` (delAlarmDatas); L383 `delAlarmData` (delAlarmDatas); L404 `getCount` (refreshDelOrUnDelView); L432 `getAlarmMessageType` (alarmTypeVisible); L504 `getCamLive` (setAlarmTypeTxt); L516 `syncNewAlarmData` (setOnclickAlarm)

## com/iermu/ui/fragment/message/ConsultIermuFragment.java

- L105 OnClick_annotation: `refresh_view`
- L108 view_id_switch_case: `refresh_view`

参照：L95 `registerListener` (onCreateView); L96 `getSystemDaras` (onCreateView); L98 `getCount` (onCreateView); L101 `syncNewSystemData` (onCreateView); L110 `syncNextSystemData` (onClick); L131 `unRegisterListener` (onDestroyView); L136 `syncNextSystemData` (onRefresh); L164 `getSystemDaras` (refreshView); L167 `getCount` (refreshView); L169 `getCount` (refreshView); L174 `getCount` (refreshView)

## com/iermu/ui/fragment/personal/BindEmailFragment.java

- L94 OnClick_annotation: `commit_confirm`
- L98 view_id_switch_case: `commit_confirm`

参照：L83 `registerListener` (onCreateView); L91 `unRegisterListener` (onDestroyView); L100 `bindNewEmail` (onClick)

## com/iermu/ui/fragment/personal/BindMobileFragment.java

- L147 OnClick_annotation: `get_message_code`, `commit_confirm`, `clear_text_img`
- L82 view_id_switch_case: `mobile_num`
- L89 view_id_switch_case: `message_code`
- L152 view_id_switch_case: `clear_text_img`
- L155 view_id_switch_case: `get_message_code`
- L163 view_id_switch_case: `commit_confirm`

参照：L142 `registerListener` (onCreateView); L143 `registerListener` (onCreateView); L157 `getMobileVerify` (onClick); L165 `bindNewMobile` (onClick); L181 `unRegisterListener` (onDestroyView); L182 `unRegisterListener` (onDestroyView)

## com/iermu/ui/fragment/personal/CertificationMobileFragment.java

- L105 OnClick_annotation: `get_message_code`, `bind_btn`, `clear_view`
- L109 view_id_switch_case: `clear_view`
- L112 view_id_switch_case: `get_message_code`
- L119 view_id_switch_case: `bind_btn`

参照：L100 `registerListener` (onCreateView); L101 `registerListener` (onCreateView); L117 `getMobileVerify` (onClick); L124 `bindMobile` (onClick); L132 `unRegisterListener` (onDestroyView); L133 `unRegisterListener` (onDestroyView)

## com/iermu/ui/fragment/personal/ChangePasswordFragment.java

- L107 OnClick_annotation: `save_perfect`, `actionbar_back`, `current_pass_close`, `new_pass_close`, `check_btn`
- L110 view_id_switch_case: `actionbar_back`
- L115 view_id_switch_case: `current_pass_close`
- L118 view_id_switch_case: `new_pass_close`
- L121 view_id_switch_case: `save_perfect`
- L151 view_id_switch_case: `check_btn`
- L256 view_id_switch_case: `current_pass`
- L269 view_id_switch_case: `new_pass`

参照：L94 `registerListener` (onCreateView); L143 `updatePassword` (onClick); L174 `unRegisterListener` (onDestroyView); L304 `logout` (onClick)

## com/iermu/ui/fragment/personal/ChangeUserNameFragment.java

- L150 OnClick_annotation: `actionbar_finish`, `actionbar_back`, `name_close`
- L153 view_id_switch_case: `actionbar_back`
- L157 view_id_switch_case: `actionbar_finish`
- L202 view_id_switch_case: `name_close`

参照：L112 `registerListener` (onCreateView); L113 `registerListener` (onCreateView); L163 `updateUserRemarkName` (onClick); L166 `updateUserName` (onClick); L190 `updateUserRemarkName` (onClick); L193 `updateUserName` (onClick); L219 `unRegisterListener` (onDestroyView); L220 `unRegisterListener` (onDestroyView); L311 `getUserInfo` (run)

## com/iermu/ui/fragment/personal/CompletePersonalFragment.java

- L129 OnClick_annotation: `hide_or_show_btn`, `close_perfect_person`, `save_perfect_person`, `mail_close`, `nickname_close`
- L132 view_id_switch_case: `mail_close`
- L135 view_id_switch_case: `hide_or_show_btn`
- L143 view_id_switch_case: `close_perfect_person`
- L147 view_id_switch_case: `save_perfect_person`
- L176 view_id_switch_case: `nickname_close`
- L348 view_id_switch_case: `input_mail`
- L355 view_id_switch_case: `input_nickname`

参照：L101 `registerListener` (onCreateView); L168 `completeUserInfo` (onClick); L185 `unRegisterListener` (onDestroyView)

## com/iermu/ui/fragment/personal/EmailVerifyFragment.java

- L141 OnClick_annotation: `commit`, `get_message_code`, `mobile_verify`, `contact_services`
- L146 view_id_switch_case: `get_message_code`
- L159 view_id_switch_case: `commit`
- L172 view_id_switch_case: `mobile_verify`
- L189 view_id_switch_case: `contact_services`

参照：L110 `registerListener` (onCreateView); L111 `registerListener` (onCreateView); L121 `unRegisterListener` (onDestroyView); L122 `unRegisterListener` (onDestroyView); L149 `getVerifyToUpdateMobile` (onClick); L152 `getVerifyFormEmail` (onClick); L162 `checkAuthCodeToUpdateMobile` (onClick); L165 `checkEmailAuthCode` (onClick)

## com/iermu/ui/fragment/personal/FeedBackFragment.java

- L87 OnClick_annotation: `actionbar_finish`, `actionbar_back`
- L90 view_id_switch_case: `actionbar_back`
- L94 view_id_switch_case: `actionbar_finish`

参照：L83 `registerListener` (onCreateView); L107 `feedBack` (onClick); L127 `unRegisterListener` (onDestroyView)

## com/iermu/ui/fragment/personal/MobileVerifyFragment.java

- L152 OnClick_annotation: `get_message_code`, `commit`, `email_verify`, `contact_services`, `clear_text_img`
- L89 view_id_switch_case: `mobile_num`
- L96 view_id_switch_case: `message_code`
- L158 view_id_switch_case: `clear_text_img`
- L161 view_id_switch_case: `get_message_code`
- L177 view_id_switch_case: `commit`
- L193 view_id_switch_case: `contact_services`
- L206 view_id_switch_case: `email_verify`

参照：L129 `registerListener` (onCreateView); L130 `registerListener` (onCreateView); L167 `getBindMobileVerify` (onClick); L170 `getVerifyToUpdateEmail` (onClick); L183 `checkAuthCode` (onClick); L186 `checkAuthCodeToUpdateEmail` (onClick); L237 `unRegisterListener` (onDestroyView); L238 `unRegisterListener` (onDestroyView)

## com/iermu/ui/fragment/personal/QrCodeScanResultFragment.java

- L67 OnClick_annotation: `qr_code_scan_sure`, `qr_code_scan_cancel`
- L70 view_id_switch_case: `qr_code_scan_sure`
- L73 view_id_switch_case: `qr_code_scan_cancel`

参照：L63 `registerListener` (onCreateView); L71 `qrCodeConfirm` (onClick); L74 `qrCodeConfirm` (onClick); L87 `unRegisterListener` (onDestroyView)

## com/iermu/ui/fragment/personal/SetPasswordFragment.java

- L84 OnClick_annotation: `save_perfect`, `actionbar_back`, `new_pass_close`, `check_btn`
- L87 view_id_switch_case: `actionbar_back`
- L91 view_id_switch_case: `new_pass_close`
- L94 view_id_switch_case: `save_perfect`
- L122 view_id_switch_case: `check_btn`

参照：L80 `registerListener` (onCreateView); L114 `setMobileLoginPassword` (onClick); L143 `unRegisterListener` (onDestroyView)

## com/iermu/ui/fragment/personal/UpdateMobileEmailFragment.java

- L96 OnClick_annotation: `update_btn`
- L99 view_id_switch_case: `update_btn`

## com/iermu/ui/fragment/personal/UserInfoFragment.java

- L167 OnClick_annotation: `choose_img`, `cancel`, `account_avatar_view`, `account_nickname_view`, `account_baidu_view`, `account_passowrd_view`, `account_email_view`, `btn_logout`, `account_mobile_view`, `i_know`
- L170 view_id_switch_case: `i_know`
- L173 view_id_switch_case: `account_avatar_view`
- L182 view_id_switch_case: `account_nickname_view`
- L192 view_id_switch_case: `account_mobile_view`
- L203 view_id_switch_case: `account_baidu_view`
- L218 view_id_switch_case: `account_email_view`
- L235 view_id_switch_case: `account_passowrd_view`
- L246 view_id_switch_case: `btn_logout`

参照：L153 `registerListener` (onCreateView); L154 `getThirdConnect` (onCreateView); L163 `getThirdConnect` (onHiddenChanged); L256 `unRegisterListener` (onDestroyView); L331 `getAccessToken` (uploadAvatar); L390 `logout` (onClick); L416 `getBaiduUid` (refreshView)

## com/iermu/ui/fragment/personal/VerificationEmailFragment.java

- L90 OnClick_annotation: `update_btn`, `get_email_verify_again`
- L93 view_id_switch_case: `update_btn`
- L97 view_id_switch_case: `get_email_verify_again`

参照：L67 `registerListener` (onCreateView); L75 `unRegisterListener` (onHiddenChanged); L77 `registerListener` (onHiddenChanged); L87 `unRegisterListener` (onDestroyView); L98 `activeEmail` (onClick)

## com/iermu/ui/fragment/personal/photos/PhotoFragment.java

- L184 OnClick_annotation: `back`, `all_txt`, `print_screen_txt`, `film_edit_txt`, `del_btn`, `undel_btn`
- L187 view_id_switch_case: `back`
- L190 view_id_switch_case: `del_btn`
- L193 view_id_switch_case: `undel_btn`
- L196 view_id_switch_case: `all_txt`
- L199 view_id_switch_case: `print_screen_txt`
- L202 view_id_switch_case: `film_edit_txt`

参照：L138 `registerListener` (initView); L139 `getThirdConnect` (initView); L181 `unRegisterListener` (onDestroyView); L357 `getUserInfo` (getPhotoNum); L362 `getScreenClip` (getPhotoNum)

## com/iermu/ui/fragment/personal/photos/PictureFragment.java

- L150 OnClick_annotation: `select_all`, `delete_btn`
- L153 view_id_switch_case: `select_all`
- L156 view_id_switch_case: `delete_btn`

参照：L107 `registerListener` (onHiddenChanged); L115 `registerListener` (initView); L116 `registerListener` (initView); L117 `registerListener` (initView); L121 `getCount` (initView); L136 `registerListener` (updateData); L137 `getScreenClip` (updateData); L140 `getAllPicAndRecord` (updateData); L142 `getUserScreenCapture` (updateData); L144 `getUserRecordClip` (updateData); L173 `deleteScreenClip` (onClick); L200 `unRegisterListener` (onItemClick); L234 `unRegisterListener` (onDestroyView); L235 `unRegisterListener` (onDestroyView); L236 `unRegisterListener` (onDestroyView); L274 `getScreenClip` (refreshViewAdapter); L312 `getCount` (isDataEmpty); L351 `getAllPicAndRecord` (getDataFromPhoto); L353 `getUserScreenCapture` (getDataFromPhoto); L355 `getUserRecordClip` (getDataFromPhoto)

## com/iermu/ui/fragment/personal/photos/ScreenFilmFragment.java

- L160 OnClick_annotation: `actionbar_back`, `deleteButton`, `share_friends`, `share_wechat`, `share_moments`, `share_cancel`
- L163 view_id_switch_case: `actionbar_back`
- L166 view_id_switch_case: `deleteButton`
- L182 view_id_switch_case: `share_friends`
- L185 view_id_switch_case: `share_wechat`
- L192 view_id_switch_case: `share_moments`
- L199 view_id_switch_case: `share_cancel`

参照：L124 `registerListener` (onCreateView); L208 `unRegisterListener` (onDestroyView); L238 `getCamLive` (initItem); L293 `deleteScreenClip` (deletePhotoOrFilm)

## com/iermu/ui/fragment/setupdev/AdvancedConfigFragment.java

- L110 OnClick_annotation: `actionbar_back`
- L113 view_id_switch_case: `actionbar_back`

参照：L90 `getAdvancedConfig` (initView); L96 `setAdvanceConfig` (onCheckedChanged); L99 `getIpMode` (initView); L201 `setIpMode` (getDevIp)

## com/iermu/ui/fragment/setupdev/ConfigWifiFragment.java

- L403 OnClick_annotation: `change_wifi_bt`, `change_hiwifi_bt`, `hide_or_show_btn`, `start_configurtion`, `hiwifi_conn_help`, `actionbar_finish`, `actionbar_back`
- L406 view_id_switch_case: `actionbar_back`
- L409 view_id_switch_case: `actionbar_finish`
- L416 view_id_switch_case: `hide_or_show_btn`
- L424 view_id_switch_case: `hiwifi_conn_help`
- L427 view_id_switch_case: `change_wifi_bt`
- L432 view_id_switch_case: `change_hiwifi_bt`
- L435 view_id_switch_case: `start_configurtion`
- L478 view_id_switch_case: `manual_mode`
- L483 view_id_switch_case: `ip_mode`

参照：L156 `handleMessage` (handleMessage); L172 `handleMessage` (handleMessage); L184 `handleMessage` (handleMessage); L224 `getInputWifiPa` (onCreateView); L235 `setInputWifiPa` (onCreateView); L248 `getDevAddErrorTimes` (initHiwifiView); L335 `scanWifi` (refreshView); L338 `scanConnectSSID` (refreshView); L339 `getConnectWifiType` (refreshView); L341 `isConnected` (refreshView); L438 `isConnected` (onClick); L458 `getAdvancedConfig` (onClick); L566 `isHiWiFiScan` (run)

## com/iermu/ui/fragment/setupdev/ConnAndBuyFragment.java

- L64 OnClick_annotation: `conn_dev_text`, `buy_dev_text`, `add_dev_back`, `grand_code_received_text`
- L67 view_id_switch_case: `add_dev_back`
- L70 view_id_switch_case: `conn_dev_text`
- L74 view_id_switch_case: `grand_code_received_text`
- L78 view_id_switch_case: `buy_dev_text`

参照：L52 `setInputWifiPa` (onCreateView); L53 `setRebBlueLight` (onCreateView); L54 `setDevAddErrorTimes` (onCreateView); L61 `quitSetupDev` (onDestroyView)

## com/iermu/ui/fragment/setupdev/ConnDevErrFragment.java

- L96 OnClick_annotation: `buttonLoad`
- L99 view_id_switch_case: `buttonLoad`

参照：L82 `quitSetupDev` (onCreateView)

## com/iermu/ui/fragment/setupdev/ConnDevFragment.java

- L268 OnClick_annotation: `actionbar_finish`
- L271 view_id_switch_case: `actionbar_finish`

参照：L240 `getCamDescription` (onCreateView); L241 `getCamTimezone` (onCreateView); L242 `registerListener` (onCreateView); L244 `addSetupDevListener` (onCreateView); L245 `registerListener` (onCreateView); L246 `getLocation` (onCreateView); L254 `quitSetupDev` (onDestroyView); L255 `removeSetupDevListener` (onDestroyView); L256 `unRegisterListener` (onDestroyView); L257 `unRegisterListener` (onDestroyView); L292 `quitSetupDev` (onClick); L314 `getCamLive` (onHiddenChanged); L487 `getCurrentLocation` (onGetLocation)

## com/iermu/ui/fragment/setupdev/DevicesFragment.java

- L198 OnClick_annotation: `actionbar_finish`, `find_help`, `conn_camera_lay`, `actionbar_back`
- L202 view_id_switch_case: `actionbar_back`
- L205 view_id_switch_case: `actionbar_finish`
- L209 view_id_switch_case: `conn_camera_lay`
- L226 view_id_switch_case: `find_help`

参照：L163 `addSetupDevListener` (onCreateView); L189 `getContext` (initViewPager); L207 `quitScanCam` (onClick); L218 `connectCam` (onClick); L243 `addSetupDevListener` (onHiddenChanged); L244 `scanCam` (onHiddenChanged); L260 `getScanCamDev` (refreshView); L275 `getCount` (refreshView); L290 `getCount` (onPageSelected)

## com/iermu/ui/fragment/setupdev/FindDevNumFragment.java

- L53 OnClick_annotation: `buttonOk`
- L56 view_id_switch_case: `buttonOk`

## com/iermu/ui/fragment/setupdev/FindNoDevFragment.java

- L47 OnClick_annotation: `cancel`, `add_type_change`, `actionbar_back`
- L51 view_id_switch_case: `actionbar_back`
- L54 view_id_switch_case: `cancel`
- L58 view_id_switch_case: `add_type_change`

参照：L56 `quitScanCam` (onClick); L59 `getDevAddErrorTimes` (onClick); L60 `setDevAddErrorTimes` (onClick)

## com/iermu/ui/fragment/setupdev/ManualConfigFragment.java

- L87 OnClick_annotation: `config_btn`, `change_wifi`, `actionbar_back`
- L90 view_id_switch_case: `actionbar_back`
- L93 view_id_switch_case: `config_btn`
- L104 view_id_switch_case: `change_wifi`

参照：L99 `notifyManualAP` (onClick); L112 `scanConnectSSID` (refreshView)

## com/iermu/ui/fragment/setupdev/ResetDevFragment.java

- L135 OnClick_annotation: `cancel`, `reset_dev`, `actionbar_back`
- L139 view_id_switch_case: `actionbar_back`
- L142 view_id_switch_case: `cancel`
- L146 view_id_switch_case: `reset_dev`

参照：L70 `handleMessage` (handleMessage); L126 `getContext` (initViewPager); L144 `quitScanCam` (onClick)

## com/iermu/ui/fragment/setupdev/ScanDevFragment.java

- L152 OnClick_annotation: `actionbar_back`
- L155 view_id_switch_case: `actionbar_back`

参照：L74 `removeSetupDevListener` (onScanCamList); L146 `addSetupDevListener` (onCreateView); L147 `scanCam` (onCreateView); L167 `addSetupDevListener` (onHiddenChanged); L168 `scanCam` (onHiddenChanged); L179 `quitScanCam` (onDestroyView); L208 `quitScanCam` (onDismiss)

## com/iermu/ui/fragment/setupdev/SearchGuideDevFragment.java

- L139 OnClick_annotation: `actionbar_back`, `start_configurtion`, `no_blue_or_red`
- L142 view_id_switch_case: `actionbar_back`
- L145 view_id_switch_case: `start_configurtion`
- L151 view_id_switch_case: `no_blue_or_red`

参照：L40 `handleMessage` (handleMessage); L96 `getRebBlueLight` (onCreateView); L107 `setRebBlueLight` (onCreateView); L109 `scanWifi` (onCreateView)

## com/iermu/ui/fragment/share/CapsuleFragment.java

- L189 OnClick_annotation: `share_capsule`, `share_wechat`, `share_moments`, `share_cancel`
- L192 view_id_switch_case: `share_capsule`
- L206 view_id_switch_case: `share_wechat`
- L211 view_id_switch_case: `share_moments`
- L216 view_id_switch_case: `share_cancel`

## com/iermu/ui/fragment/share/ShareScreenCapFragment.java

- L110 OnClick_annotation: `actionbar_back`, `deleteButton`, `video_play`
- L113 view_id_switch_case: `actionbar_back`
- L116 view_id_switch_case: `deleteButton`
- L133 view_id_switch_case: `video_play`

## com/iermu/ui/fragment/share/auth/AuthPermissionFragment.java

- L81 OnClick_annotation: `back`, `btn_ok`
- L85 view_id_switch_case: `back`
- L88 view_id_switch_case: `btn_ok`

参照：L78 `registerListener` (initView); L106 `unRegisterListener` (onDestroyView); L121 `getGrantCode` (shareTypeWx); L125 `getCamDescription` (shareTypeCode)

## com/iermu/ui/fragment/share/auth/AuthUserFragment.java

- L164 OnClick_annotation: `wx_btn`, `share_wx`, `actionbar_back`, `grand_code_btn`, `share_gc`
- L167 view_id_switch_case: `actionbar_back`
- L176 view_id_switch_case: `share_wx`
- L177 view_id_switch_case: `wx_btn`
- L181 view_id_switch_case: `grand_code_btn`
- L182 view_id_switch_case: `share_gc`

参照：L136 `getUserInfo` (onCreateView); L138 `registerListener` (onCreateView); L139 `registerListener` (onCreateView); L140 `syncGrantUsers` (onCreateView); L150 `registerListener` (onHiddenChanged); L152 `unRegisterListener` (onHiddenChanged); L160 `unRegisterListener` (onDestroyView); L161 `unRegisterListener` (onDestroyView); L254 `getCount` (onMenuItemClick); L259 `dropGrantUser` (onMenuItemClick); L280 `getGrantUser` (refreshView)

## com/iermu/ui/fragment/share/auth/AuthUserInfoFragment.java

- L142 OnClick_annotation: `remark_name`, `cancel_share`, `back`
- L145 view_id_switch_case: `back`
- L148 view_id_switch_case: `remark_name`
- L154 view_id_switch_case: `cancel_share`

参照：L105 `syncGrantUsers` (onCreateView); L106 `registerListener` (onCreateView); L107 `registerListener` (onCreateView); L138 `unRegisterListener` (onDestroyView); L139 `unRegisterListener` (onDestroyView); L161 `dropGrantUser` (onClick); L206 `getGrantUser` (onGrantUser)

## com/iermu/ui/fragment/share/grandcode/GrandCodeFragment.java

- L138 OnClick_annotation: `copy_code`, `grand_code_add_more`, `actionbar_back`, `how_to_use_grand_code`
- L141 view_id_switch_case: `actionbar_back`
- L144 view_id_switch_case: `grand_code_add_more`
- L148 view_id_switch_case: `copy_code`
- L161 view_id_switch_case: `how_to_use_grand_code`

参照：L123 `registerListener` (onCreateView); L124 `getGrantCode` (onCreateView); L125 `getMineCamList` (onCreateView); L135 `unRegisterListener` (onDestroyView)

## com/iermu/ui/fragment/share/grandcode/GrandCodeHelpFragment.java

- L48 OnClick_annotation: `actionbar_back`
- L51 view_id_switch_case: `actionbar_back`

## com/iermu/ui/fragment/share/grandcode/GrandCodeMoreCamFragment.java

- L104 OnClick_annotation: `actionbar_back`, `grand_code_sure`
- L107 view_id_switch_case: `actionbar_back`
- L110 view_id_switch_case: `grand_code_sure`

参照：L86 `getMineCamListWithNoSort` (onCreateView); L93 `registerListener` (onCreateView); L100 `unRegisterListener` (onDestroy); L145 `syncNewCamList` (onRefresh); L154 `isLoadedAllCamLive` (onScrollStateChanged); L159 `syncOldCamList` (onScrollStateChanged); L176 `getMineCamListWithNoSort` (onGetCamListByCategory)

## com/iermu/ui/fragment/share/grandcode/GrandCodeReceivedFragment.java

- L120 OnClick_annotation: `actionbar_back`, `grand_code_sure`
- L123 view_id_switch_case: `actionbar_back`
- L128 view_id_switch_case: `grand_code_sure`

参照：L80 `registerListener` (onCreateView); L81 `registerListener` (onCreateView); L110 `unRegisterListener` (onDestroyView); L111 `unRegisterListener` (onDestroyView); L146 `getGrantInfo` (syncGrantInfo); L182 `syncNewCamList` (onGrantShare)

## com/iermu/ui/fragment/share/sharelive/EditCamIntroFragment.java

- L70 OnClick_annotation: `actionbar_finish`, `actionbar_back`
- L73 view_id_switch_case: `actionbar_back`
- L77 view_id_switch_case: `actionbar_finish`

## com/iermu/ui/fragment/share/sharelive/ShareImageFragment.java

- L162 OnClick_annotation: `buttonShare`, `actionbar_back`, `deleteButton`, `share_wechat`, `share_moments`, `share_cancel`
- L166 view_id_switch_case: `actionbar_back`
- L173 view_id_switch_case: `deleteButton`
- L196 view_id_switch_case: `buttonShare`
- L204 view_id_switch_case: `share_wechat`
- L209 view_id_switch_case: `share_moments`
- L214 view_id_switch_case: `share_cancel`

## com/iermu/ui/fragment/share/sharelive/SharePrivatePermissionFragment.java

- L73 OnClick_annotation: `view_no_permission`, `view_permission`, `actionbar_back`
- L76 view_id_switch_case: `actionbar_back`
- L83 view_id_switch_case: `view_no_permission`
- L88 view_id_switch_case: `view_permission`

## com/iermu/ui/fragment/share/sharelive/SharePrivateProtocolFragment.java

- L88 OnClick_annotation: `share_link`, `share_public`, `close_share`
- L91 view_id_switch_case: `share_link`
- L95 view_id_switch_case: `share_public`
- L108 view_id_switch_case: `close_share`

参照：L70 `registerListener` (onCreateView); L85 `unRegisterListener` (onDestroyView); L96 `getCamLive` (onClick); L116 `cancleShare` (onClick); L149 `getCamLive` (refreshView)

## com/iermu/ui/fragment/share/sharelive/SharePrivateSetPasswordFragment.java

- L120 OnClick_annotation: `actionbar_finish`, `view_close`
- L123 view_id_switch_case: `actionbar_finish`
- L133 view_id_switch_case: `view_close`

## com/iermu/ui/fragment/share/sharelive/SharePrivateSuccessFragment.java

- L336 OnClick_annotation: `close`, `share_wechat`, `share_moments`, `share_link`, `share_code`, `live_btn`, `share_cancel`, `close_share`, `location_view`, `cam_name_view`, `timing_view`, `permission_view`
- L339 view_id_switch_case: `close`
- L348 view_id_switch_case: `cam_name_view`
- L359 view_id_switch_case: `location_view`
- L372 view_id_switch_case: `share_link`
- L378 view_id_switch_case: `share_code`
- L384 view_id_switch_case: `permission_view`
- L395 view_id_switch_case: `close_share`
- L406 view_id_switch_case: `share_wechat`
- L412 view_id_switch_case: `share_moments`
- L418 view_id_switch_case: `share_cancel`
- L421 view_id_switch_case: `timing_view`
- L433 view_id_switch_case: `live_btn`

参照：L175 `setIsPrivateShareShowLocation` (onFragmentResult); L181 `setIsPrivateShareShowLocation` (onFragmentResult); L210 `registerListener` (onCreateView); L211 `registerListener` (onCreateView); L212 `registerListener` (onCreateView); L213 `getLocation` (onCreateView); L230 `unRegisterListener` (onDestroyView); L231 `unRegisterListener` (onDestroyView); L232 `unRegisterListener` (onDestroyView); L236 `getCamLive` (refreshView); L363 `getCamLive` (onClick); L402 `cancleShare` (onClick); L439 `createShare` (onClick); L483 `createShare` (createShare); L483 `getJsonLocation` (createShare); L495 `getCamLive` (share); L501 `getCamLive` (share); L506 `getPriShareLiveLink` (share); L516 `getPriShareLiveLink` (share); L552 `createShare` (onCancleShare); L552 `getJsonLocation` (onCancleShare); L578 `cancleShare` (onClick); L610 `getCurrentLocation` (getJsonLocation); L611 `getJsonLocation` (getJsonLocation); L621 `getCurrentLocation` (refreshLocation); L623 `isPrivateShareShowLocation` (refreshLocation); L643 `getCamLive` (callBack)

## com/iermu/ui/fragment/share/sharelive/SharePrivateTimingFragment.java

- L98 OnClick_annotation: `view_half_hour`, `view_hour`, `view_24_hour`, `view_day`, `view_no_time`
- L105 view_id_switch_case: `view_half_hour`
- L110 view_id_switch_case: `view_hour`
- L115 view_id_switch_case: `view_24_hour`
- L120 view_id_switch_case: `view_day`
- L125 view_id_switch_case: `view_no_time`

## com/iermu/ui/fragment/share/sharelive/SharePublicEditFragment.java

- L193 OnClick_annotation: `buttonOk`, `cam_name_view`, `actionbar_back`, `actionbar_finish`, `share_intro`, `location_view`, `actionbar_back_text`
- L197 view_id_switch_case: `actionbar_back`
- L201 view_id_switch_case: `actionbar_finish`
- L202 view_id_switch_case: `buttonOk`
- L218 view_id_switch_case: `actionbar_back_text`
- L221 view_id_switch_case: `cam_name_view`
- L226 view_id_switch_case: `location_view`
- L232 view_id_switch_case: `share_intro`

参照：L102 `setIsPublicShareShowLocation` (onFragmentResult); L108 `setIsPublicShareShowLocation` (onFragmentResult); L150 `registerListener` (onCreateView); L151 `registerListener` (onCreateView); L152 `registerListener` (onCreateView); L153 `registerListener` (onCreateView); L154 `getLocation` (onCreateView); L155 `getCamLive` (onCreateView); L156 `syncGetIntro` (onCreateView); L166 `unRegisterListener` (onDestroyView); L167 `unRegisterListener` (onDestroyView); L168 `unRegisterListener` (onDestroyView); L169 `unRegisterListener` (onDestroyView); L176 `unRegisterListener` (onHiddenChanged); L178 `registerListener` (onHiddenChanged); L183 `getCamLive` (refreshView); L195 `getCamLive` (onClick); L207 `createShare` (onClick); L207 `getJsonLocation` (onClick); L261 `cancleShare` (onClick); L276 `getCurrentLocation` (getJsonLocation); L277 `getJsonLocation` (getJsonLocation); L295 `getCamLive` (onShareCreated); L331 `createShare` (onCancleShare); L331 `getJsonLocation` (onCancleShare); L347 `getCurrentLocation` (refreshLocation); L349 `isPublicShareShowLocation` (refreshLocation)

## com/iermu/ui/fragment/share/sharelive/SharePublicProtocolFragment.java

- L85 OnClick_annotation: `buttonBegin`, `public_live`, `actionbar_back`
- L88 view_id_switch_case: `actionbar_back`
- L91 view_id_switch_case: `buttonBegin`

## com/iermu/ui/fragment/share/sharelive/SharePublicSuccessFragment.java

- L127 OnClick_annotation: `share_wx`, `share_peng`, `share_link`, `share_code`, `cancle_share`, `cam_live`, `actionbar_finish`
- L130 view_id_switch_case: `actionbar_back`
- L133 view_id_switch_case: `actionbar_finish`
- L137 view_id_switch_case: `share_wx`
- L140 view_id_switch_case: `share_peng`
- L143 view_id_switch_case: `share_link`
- L147 view_id_switch_case: `share_code`
- L150 view_id_switch_case: `cancle_share`
- L157 view_id_switch_case: `cam_live`

参照：L98 `registerListener` (onCreateView); L99 `registerListener` (onCreateView); L100 `getCamLive` (onCreateView); L123 `unRegisterListener` (onDestroyView); L124 `unRegisterListener` (onDestroyView); L155 `cancleShare` (onClick); L158 `getCamLive` (onClick); L179 `createShare` (onShareClick); L179 `getJsonLocation` (onShareClick); L183 `getCurrentLocation` (getJsonLocation); L185 `getJsonLocation` (getJsonLocation); L202 `getCamLive` (onShareCreated); L208 `getCamLive` (onShareCreated); L214 `getPubShareLiveLink` (onShareCreated); L226 `getPubShareLiveLink` (onShareCreated)

## com/iermu/ui/fragment/zxing/CaptureActivity.java


参照：L100 `init` (onCreate); L111 `registerListener` (onCreate); L112 `registerListener` (onCreate); L267 `unRegisterListener` (onDestroy); L268 `unRegisterListener` (onDestroy); L320 `check433Name` (handleDecode); L327 `getQrCodeScanInfo` (handleDecode); L457 `onQRscanListener` (onCheck433Name); L485 `getQrCodeScanInfo` (onQrCodeScanInfo); L488 `onGetQrCodeScanResult` (onQrCodeScanInfo)

## com/iermu/ui/fragment/zxing/camera/CameraConfigurationManager.java


参照：L64 `invoke` (setDisplayOrientation)

## com/iermu/ui/fragment/zxing/camera/FlashlightManager.java


参照：L32 `invoke` (getHardwareService); L35 `invoke` (getHardwareService); L70 `invoke` (invoke); L93 `invoke` (setFlashlight)

## com/iermu/ui/fragment/zxing/decoding/CaptureActivityHandler.java

- L43 view_id_switch_case: `auto_focus`
- L49 view_id_switch_case: `decode_failed`
- L53 view_id_switch_case: `decode_succeeded`
- L60 view_id_switch_case: `launch_product_query`
- L67 view_id_switch_case: `restart_preview`
- L71 view_id_switch_case: `return_scan_result`

## com/iermu/ui/fragment/zxing/decoding/DecodeHandler.java

- L34 view_id_switch_case: `decode`
- L37 view_id_switch_case: `quit`

## com/iermu/ui/fragment/zxing/view/ViewfinderView.java


参照：L74 `getContext` (onDraw)

## com/iermu/ui/util/AmapUtil.java


参照：L25 `getLocation` (onLocationChanged)

## com/iermu/ui/util/CheckVersionUtils.java


参照：L50 `getAlertedUpdateVersion` (onCheckUpdateCallback); L53 `setAlertedUpdateVersion` (onCheckUpdateCallback)

## com/iermu/ui/util/LogoutUtil.java


参照：L16 `logout` (logout)

## com/iermu/ui/util/PickerDialogUtil.java


参照：L66 `init` (showTimePicker); L90 `init` (showDatePicker); L122 `init` (showDateTimePicker); L154 `init` (init); L164 `init` (init)

## com/iermu/ui/util/ShareUtil.java


参照：L38 `getCamLive` (saveBitmap)

## com/iermu/ui/util/Util.java


参照：L270 `newInstance` (getStatusHeight)

## com/iermu/ui/view/AcceptAuthDialog.java

- L85 view_id_switch_case: `auth_close`
- L88 view_id_switch_case: `auth_ok`

参照：L42 `init` (onCreate)

## com/iermu/ui/view/Add433SuccessDialog.java

- L77 view_id_switch_case: `experience_later`
- L81 view_id_switch_case: `experience_now`

## com/iermu/ui/view/AddPrePositionView.java

- L177 OnClick_annotation: `textViewAddCancel`, `textViewAddSubmit`
- L137 view_id_switch_case: `radioButton1`
- L149 view_id_switch_case: `radioButton2`
- L161 view_id_switch_case: `radioButton3`
- L180 view_id_switch_case: `textViewAddCancel`
- L186 view_id_switch_case: `textViewAddSubmit`

## com/iermu/ui/view/CardRecordLiveControllerView.java

- L277 view_id_switch_case: `buttonOutFullScreen`
- L283 view_id_switch_case: `buttonPrintScreenCtrl`
- L289 view_id_switch_case: `buttonVoice`
- L296 view_id_switch_case: `buttonPlayCtrl`
- L302 view_id_switch_case: `viewSelectDateCtrl`
- L307 view_id_switch_case: `viewCut`

参照：L216 `handleMessage` (handleMessage); L244 `handleMessage` (handleMessage)

## com/iermu/ui/view/CardRepairTipsDialog.java

- L44 view_id_switch_case: `buttonClose`
- L47 view_id_switch_case: `see_detail_rl`

## com/iermu/ui/view/CloudControllerView.java

- L233 OnClick_annotation: `textViewAutoColor`, `textViewPrepositionColor`, `CloudAutoView`, `preset_get_again`
- L236 view_id_switch_case: `textViewPrepositionColor`
- L246 view_id_switch_case: `textViewAutoColor`
- L256 view_id_switch_case: `preset_get_again`
- L262 view_id_switch_case: `CloudAutoView`

参照：L108 `getContext` (updatePositions); L263 `getContext` (onClick)

## com/iermu/ui/view/CloudInformDialog.java

- L34 view_id_switch_case: `no_inform`
- L37 view_id_switch_case: `btn_buy`

参照：L38 `getContext` (onClick)

## com/iermu/ui/view/CloudOverTimeDialg.java

- L50 view_id_switch_case: `btn_ok`
- L56 view_id_switch_case: `btn_buy`

参照：L57 `getContext` (onClick)

## com/iermu/ui/view/CloudOvertimeDialog.java

- L50 view_id_switch_case: `btn_ok`
- L56 view_id_switch_case: `btn_buy`

参照：L57 `getContext` (onClick)

## com/iermu/ui/view/CloudStartCloseDialog.java


参照：L35 `getContext` (onCreate)

## com/iermu/ui/view/ConnCamDialog.java

- L155 view_id_switch_case: `view_location`
- L161 view_id_switch_case: `view_timezone`
- L168 view_id_switch_case: `view_dev_name`
- L174 view_id_switch_case: `btn_see_dev`

参照：L57 `init` (onCreate)

## com/iermu/ui/view/ContactCallDialog.java

- L74 view_id_switch_case: `contact0`
- L79 view_id_switch_case: `contact1`
- L84 view_id_switch_case: `contact2`
- L89 view_id_switch_case: `contact3`
- L94 view_id_switch_case: `contact4`
- L99 view_id_switch_case: `offical_cancel`

参照：L41 `registerListener` (onCreate); L42 `getContactList` (onCreate); L43 `init` (onCreate); L47 `getContext` (init); L65 `getContext` (init); L76 `getContext` (onClick); L81 `getContext` (onClick); L86 `getContext` (onClick); L91 `getContext` (onClick); L96 `getContext` (onClick)

## com/iermu/ui/view/LiveControllerView.java

- L170 view_id_switch_case: `buttonOutFullScreen`
- L171 view_id_switch_case: `outFullScreen`
- L177 view_id_switch_case: `buttonVoice`
- L183 view_id_switch_case: `buttonBack`
- L189 view_id_switch_case: `buttonFullScreen`
- L195 view_id_switch_case: `textViewZanNum`
- L202 view_id_switch_case: `buttonDanMu`

## com/iermu/ui/view/MainLiveControllerView.java

- L528 view_id_switch_case: `viewTemperature`
- L533 view_id_switch_case: `check_details`
- L546 view_id_switch_case: `buttonOutFullScreen`
- L553 view_id_switch_case: `buttonVoice`
- L560 view_id_switch_case: `viewCut`
- L572 view_id_switch_case: `imageButtonCtrlCloudAuto`
- L594 view_id_switch_case: `viewCloudCtrl`
- L613 view_id_switch_case: `buttonLevelCtrl`
- L635 view_id_switch_case: `imageButtonScreenCapCtrl`
- L648 view_id_switch_case: `imageButtonPrintScreenCtrl`
- L654 view_id_switch_case: `preset_get_again_ctrl`
- L660 view_id_switch_case: `buttonSwitchNow`
- L667 view_id_switch_case: `imageViewSwitchClose`
- L671 view_id_switch_case: `screen_cap_layout`
- L683 view_id_switch_case: `imageButtonCloseTip`
- L686 view_id_switch_case: `radioButtonLevel1`
- L692 view_id_switch_case: `radioButtonLevel2`
- L698 view_id_switch_case: `radioButtonLevel3`
- L1378 view_id_switch_case: `radioButtonLevel1`
- L1381 view_id_switch_case: `radioButtonLevel2`
- L1384 view_id_switch_case: `radioButtonLevel3`

参照：L573 `getContext` (onClick); L1174 `isCapsuleDialogShow` (setCapsule); L1216 `setCapsuleDialogShow` (showCapsuleDialog); L1217 `getContext` (showCapsuleDialog); L1218 `getContext` (showCapsuleDialog); L1247 `handleMessage` (handleMessage); L1519 `getContext` (onTopTouch); L1528 `getContext` (updatePositions)

## com/iermu/ui/view/MineLiveGuid.java

- L67 view_id_switch_case: `guid_double_click_view`
- L71 view_id_switch_case: `guid_on_long_press`
- L75 view_id_switch_case: `guid_cloud_auto`

参照：L38 `init` (onCreate); L43 `getContext` (init); L58 `getContext` (init)

## com/iermu/ui/view/MyViewPager.java


参照：L159 `getContext` (initViewPager); L237 `getCount` (setCurrentItemInternal); L247 `getCount` (setCurrentItemInternal); L248 `getCount` (setCurrentItemInternal); L316 `getContext` (setPageMarginDrawable); L383 `getCount` (dataSetChanged); L396 `getCount` (dataSetChanged); L424 `getCount` (populate); L767 `getCount` (onInterceptTouchEvent); L800 `getCount` (onTouchEvent); L855 `getCount` (onTouchEvent); L912 `getCount` (draw); L926 `getCount` (draw); L1006 `getCount` (fakeDragBy); L1136 `getCount` (pageRight)

## com/iermu/ui/view/NetSpeed.java


参照：L38 `getUid` (getNetworkRxBytes)

## com/iermu/ui/view/PersonChooseImgDialog.java

- L58 view_id_switch_case: `cancel`
- L61 view_id_switch_case: `choose_img`

参照：L31 `init` (onCreate); L39 `getContext` (init); L49 `getContext` (init)

## com/iermu/ui/view/PersonOfficalPhone.java

- L60 view_id_switch_case: `offical_phone`
- L68 view_id_switch_case: `offical_cancel`

参照：L33 `init` (onCreate); L37 `getContext` (init); L50 `getContext` (init); L64 `getContext` (onClick); L66 `getContext` (onClick)

## com/iermu/ui/view/PinnedSectionListView.java


参照：L161 `getContext` (initView); L239 `getCount` (ensureShadowForPosition); L260 `getCount` (findFirstVisibleSectionPosition); L279 `getCount` (findCurrentSectionPosition); L305 `getCount` (recreatePinnedShadow)

## com/iermu/ui/view/PublicCamStatusView.java

- L241 view_id_switch_case: `imageViewClose`
- L244 view_id_switch_case: `buttonStart`
- L250 view_id_switch_case: `buttonSwitchNow`
- L257 view_id_switch_case: `imageViewSwitchClose`
- L261 view_id_switch_case: `imageViewRefresh`
- L267 view_id_switch_case: `buttonResolve`
- L273 view_id_switch_case: `viewAnotherUserClose`

## com/iermu/ui/view/PublicShareLiveDialog.java

- L92 view_id_switch_case: `clear_text_img`
- L95 view_id_switch_case: `close`
- L100 view_id_switch_case: `start_live`

参照：L102 `isConnected` (onClick)

## com/iermu/ui/view/PullableStickyListHeadersListView.java


参照：L26 `getCount` (canPullDown); L35 `getCount` (canPullUp); L38 `getCount` (canPullUp)

## com/iermu/ui/view/RecordDefinitionDialog.java

- L66 view_id_switch_case: `super_definition`
- L72 view_id_switch_case: `high_definition`
- L78 view_id_switch_case: `low_definition`
- L84 view_id_switch_case: `record_cancel`

参照：L35 `init` (onCreate); L43 `getContext` (init); L57 `getContext` (init)

## com/iermu/ui/view/RecordLiveControllerView.java

- L286 view_id_switch_case: `buttonOutFullScreen`
- L292 view_id_switch_case: `buttonPrintScreenCtrl`
- L298 view_id_switch_case: `buttonVoice`
- L305 view_id_switch_case: `buttonPlayCtrl`
- L311 view_id_switch_case: `viewCut`
- L317 view_id_switch_case: `buttonCloudCtrl`
- L323 view_id_switch_case: `buttonSelectDateCtrl`
- L328 view_id_switch_case: `viewTimeLineCtrl`
- L330 view_id_switch_case: `viewThumbnailCtrl`

## com/iermu/ui/view/RecordModeDialog.java

- L62 view_id_switch_case: `record_cancel`
- L65 view_id_switch_case: `record_continue`
- L71 view_id_switch_case: `record_change`

参照：L33 `init` (onCreate); L41 `getContext` (init); L53 `getContext` (init)

## com/iermu/ui/view/SDFormatDialog.java

- L58 view_id_switch_case: `card_format`
- L64 view_id_switch_case: `card_cancel`

参照：L31 `init` (onCreate); L39 `getContext` (init); L49 `getContext` (init)

## com/iermu/ui/view/SavePictureDialog.java

- L52 view_id_switch_case: `offical_cancel`

参照：L27 `init` (onCreate); L31 `getContext` (init); L43 `getContext` (init)

## com/iermu/ui/view/ShareLiveDialog.java

- L100 view_id_switch_case: `close`
- L106 view_id_switch_case: `save_qr`

参照：L59 `init` (onCreate)

## com/iermu/ui/view/SwitchButton.java


参照：L57 `init` (<constructor_or_field_initializer>)

## com/iermu/ui/view/UpgradeTipDialog.java

- L85 view_id_switch_case: `buttonClose`
- L88 view_id_switch_case: `cam_upgrade_cancle`
- L92 view_id_switch_case: `cam_upgrade_sure`

参照：L35 `init` (<constructor_or_field_initializer>)

## com/iermu/ui/view/filmEdit/FilmEditProgressDialog.java


参照：L29 `initDate` (initView)

## com/iermu/ui/view/kankan/wheel/widget/ItemsRange.java


参照：L22 `getCount` (getLast)

## com/iermu/ui/view/kankan/wheel/widget/WheelView.java


参照：L111 `initData` (<constructor_or_field_initializer>); L176 `initData` (<constructor_or_field_initializer>); L241 `initData` (<constructor_or_field_initializer>); L245 `getContext` (initData); L391 `getContext` (setWheelForeground); L409 `getContext` (initResourcesIfNecessary); L641 `getCount` (rebuildItems); L651 `getCount` (rebuildItems); L669 `getContext` (createItemsLayout)

## com/iermu/ui/view/timeline/HorizontalListView.java


参照：L171 `getContext` (initView); L173 `getContext` (initView); L187 `getContext` (initViewForSpecific); L260 `getCount` (setSelectionFrom); L313 `getCount` (lookForSelectablePosition); L484 `getCount` (fillListRight); L487 `getCount` (fillListRight); L491 `getCount` (fillListRight); L581 `getCount` (reportScroll)

## com/iermu/ui/view/timeline/TimeLineLinearLayout.java


参照：L47 `getContext` (setViewH)

## com/iermu/ui/view/timeline/TimeSpanViewLinearLayout.java


参照：L90 `init` (<constructor_or_field_initializer>); L94 `getContext` (init); L167 `getContext` (drawScaleLine); L174 `getContext` (drawScaleLine); L175 `getContext` (drawScaleLine); L176 `getContext` (drawScaleLine); L180 `getContext` (drawScaleLine); L197 `getContext` (drawScaleLine); L214 `getContext` (drawScaleLine); L225 `getContext` (drawScaleLineT); L232 `getContext` (drawScaleLineT); L233 `getContext` (drawScaleLineT); L234 `getContext` (drawScaleLineT); L238 `getContext` (drawScaleLineT); L254 `getContext` (drawScaleLineT); L269 `getContext` (drawScaleLineT)
