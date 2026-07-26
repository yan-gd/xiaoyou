# 情侣聊天成就页设计 QA

## 对照基线

- 选定设计稿：`C:\Users\qq157\.codex\generated_images\019f7ea8-d255-7340-9adb-21da946c6cad\call_XcBAPJhU2m0g4aHjYMhtDdhq.png`
- vivo 真机截图：`tooling/achievement-qa-ornate-v3.png`
- 同屏对照图：`tooling/achievement-design-comparison-v3.png`
- 真机视口：1260 × 2800，Android 16

## 视觉资产

- `assets/achievements/storybook-background.png`：完整古典绘本背景、丝带、金色雕花和柔光纹理。
- `assets/achievements/level-medallion.png`：关系等级心形宝石徽章。
- `assets/achievements/jewel-frame.png`：章节和成就共用的透明宝石勋章框。
- `assets/achievements/treasure-box.png`：章节进度花藤宝箱。

以上位图均由内置 ImageGen 依据选定设计稿定向生成，不使用占位图、Emoji 或代码绘制的伪素材。

## 已核对

- 页面保持设计稿的“成就故事书”信息层级：等级总览、章节轨道、章节详情、成就条目。
- 珍珠粉、香槟金、雾紫和柔光材质与设计稿一致；流动渐变灯带用于总览卡、当前章节和当前成就。
- 6 个章节共 72 项成就，每章 12 项；锁定、隐藏、进行中、已解锁及稀有度都有独立反馈。
- 标题、章节、进度和卡片正文在真机上无越界或裁切。
- 左右列表拥有独立滚动上下文，切换章节后回到对应章节顶部。
- 灯带、微光和粒子动画限制在独立重绘区域内，避免滚动时整页重绘。
- 成就统计只读取本机已经加载的结构化聊天数据，不上传服务器，也不使用关键词或正则判断聊天语义。

## 验证

- `flutter analyze`：通过，无问题。
- `flutter test`：11 项测试全部通过。
- Debug APK：构建成功并在 vivo V2324A 完成真机视觉检查。

final result: passed
