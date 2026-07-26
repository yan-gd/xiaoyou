import 'dart:math';

import 'package:flutter/material.dart';

import 'chat_models.dart';

enum AchievementChapter {
  firstLight,
  dailyLife,
  chemistry,
  sharing,
  care,
  forever,
}

extension AchievementChapterPresentation on AchievementChapter {
  String get title => switch (this) {
        AchievementChapter.firstLight => '初见微光',
        AchievementChapter.dailyLife => '日常相伴',
        AchievementChapter.chemistry => '心动默契',
        AchievementChapter.sharing => '分享生活',
        AchievementChapter.care => '彼此守护',
        AchievementChapter.forever => '长久陪伴',
      };

  String get subtitle => switch (this) {
        AchievementChapter.firstLight => '故事从第一句话开始',
        AchievementChapter.dailyLife => '把普通日子聊成共同生活',
        AchievementChapter.chemistry => '一来一回之间越来越懂彼此',
        AchievementChapter.sharing => '声音、照片和此刻都愿意分享',
        AchievementChapter.care => '在早晚与沉默之间陪着对方',
        AchievementChapter.forever => '时间会把相伴写成很长的故事',
      };

  IconData get icon => switch (this) {
        AchievementChapter.firstLight => Icons.local_florist_rounded,
        AchievementChapter.dailyLife => Icons.local_cafe_rounded,
        AchievementChapter.chemistry => Icons.favorite_rounded,
        AchievementChapter.sharing => Icons.photo_camera_rounded,
        AchievementChapter.care => Icons.shield_rounded,
        AchievementChapter.forever => Icons.all_inclusive_rounded,
      };
}

enum AchievementRarity {
  common,
  refined,
  precious,
  epic,
  legendary,
  secret,
}

extension AchievementRarityPresentation on AchievementRarity {
  String get label => switch (this) {
        AchievementRarity.common => '普通',
        AchievementRarity.refined => '精致',
        AchievementRarity.precious => '珍贵',
        AchievementRarity.epic => '史诗',
        AchievementRarity.legendary => '传说',
        AchievementRarity.secret => '秘密',
      };
}

enum AchievementMetric {
  totalMessages,
  activeDays,
  mutualDays,
  replyTurns,
  quickReplies,
  conversationSessions,
  longSessions,
  bestDailyMessages,
  bestStreak,
  imageMessages,
  voiceMessages,
  voiceSeconds,
  stickerMessages,
  mediaKinds,
  morningMessages,
  nightMessages,
  morningMutualDays,
  nightMutualDays,
  proactiveMessages,
  favoriteMessages,
  relationshipDays,
  textCharacters,
}

class RelationshipAchievementStats {
  RelationshipAchievementStats._(this._values);

  final Map<AchievementMetric, int> _values;

  int value(AchievementMetric metric) => _values[metric] ?? 0;

  factory RelationshipAchievementStats.fromMessages(
    Iterable<ChatMessage> source, {
    required int favoriteCount,
    DateTime? now,
  }) {
    final messages = source
        .where(
          (message) =>
              message.createdAt > 0 && message.localState != 'cancelled',
        )
        .toList()
      ..sort((a, b) => a.createdAt.compareTo(b.createdAt));
    if (messages.isEmpty) {
      return RelationshipAchievementStats._({
        for (final metric in AchievementMetric.values) metric: 0,
        AchievementMetric.favoriteMessages: max(0, favoriteCount),
      });
    }

    final activeDates = <DateTime>{};
    final rolesByDate = <DateTime, Set<String>>{};
    final morningRolesByDate = <DateTime, Set<String>>{};
    final nightRolesByDate = <DateTime, Set<String>>{};
    final messagesByDate = <DateTime, int>{};
    final mediaKinds = <String>{};
    var replyTurns = 0;
    var quickReplies = 0;
    var conversationSessions = 0;
    var longSessions = 0;
    var currentSessionMessages = 0;
    var imageMessages = 0;
    var voiceMessages = 0;
    var voiceSeconds = 0;
    var stickerMessages = 0;
    var morningMessages = 0;
    var nightMessages = 0;
    var proactiveMessages = 0;
    var textCharacters = 0;

    ChatMessage? previous;
    for (final message in messages) {
      final timestamp = message.timestamp;
      final date = DateTime(timestamp.year, timestamp.month, timestamp.day);
      activeDates.add(date);
      rolesByDate.putIfAbsent(date, () => <String>{}).add(message.role);
      messagesByDate.update(date, (value) => value + 1, ifAbsent: () => 1);
      textCharacters += message.text.runes.length;

      if (message.kind != 'text') {
        mediaKinds.add(message.kind);
      }
      switch (message.kind) {
        case 'image':
          imageMessages++;
          break;
        case 'voice':
          voiceMessages++;
          voiceSeconds += (message.durationMs / 1000).ceil();
          break;
        case 'sticker':
          stickerMessages++;
          break;
        default:
          break;
      }

      final isMorning = timestamp.hour >= 5 && timestamp.hour < 10;
      final isNight = timestamp.hour >= 22 || timestamp.hour < 5;
      if (isMorning) {
        morningMessages++;
        morningRolesByDate
            .putIfAbsent(date, () => <String>{})
            .add(message.role);
      }
      if (isNight) {
        nightMessages++;
        nightRolesByDate.putIfAbsent(date, () => <String>{}).add(message.role);
      }

      final earlier = previous;
      if (earlier == null) {
        conversationSessions = 1;
        currentSessionMessages = 1;
        if (message.fromXiaoyou) {
          proactiveMessages++;
        }
      } else {
        final gap = max(0, message.createdAt - earlier.createdAt);
        if (gap > 30 * 60) {
          if (currentSessionMessages >= 20) {
            longSessions++;
          }
          conversationSessions++;
          currentSessionMessages = 1;
        } else {
          currentSessionMessages++;
        }
        if (message.role != earlier.role && gap <= 6 * 60 * 60) {
          replyTurns++;
          if (gap <= 5 * 60) {
            quickReplies++;
          }
        }
        if (message.fromXiaoyou && gap >= 60 * 60) {
          proactiveMessages++;
        }
      }
      previous = message;
    }
    if (currentSessionMessages >= 20) {
      longSessions++;
    }

    final sortedDates = activeDates.toList()..sort();
    var bestStreak = sortedDates.isEmpty ? 0 : 1;
    var streak = bestStreak;
    for (var index = 1; index < sortedDates.length; index++) {
      final difference =
          sortedDates[index].difference(sortedDates[index - 1]).inDays;
      if (difference == 1) {
        streak++;
        bestStreak = max(bestStreak, streak);
      } else if (difference > 1) {
        streak = 1;
      }
    }

    final current = now ?? DateTime.now();
    final firstDate = messages.first.timestamp;
    final relationshipDays = max(
      1,
      DateTime(current.year, current.month, current.day)
              .difference(
                DateTime(firstDate.year, firstDate.month, firstDate.day),
              )
              .inDays +
          1,
    );
    final mutualDays =
        rolesByDate.values.where((roles) => roles.length >= 2).length;
    final morningMutualDays =
        morningRolesByDate.values.where((roles) => roles.length >= 2).length;
    final nightMutualDays =
        nightRolesByDate.values.where((roles) => roles.length >= 2).length;
    final bestDailyMessages = messagesByDate.values.fold<int>(
      0,
      max,
    );

    return RelationshipAchievementStats._({
      AchievementMetric.totalMessages: messages.length,
      AchievementMetric.activeDays: activeDates.length,
      AchievementMetric.mutualDays: mutualDays,
      AchievementMetric.replyTurns: replyTurns,
      AchievementMetric.quickReplies: quickReplies,
      AchievementMetric.conversationSessions: conversationSessions,
      AchievementMetric.longSessions: longSessions,
      AchievementMetric.bestDailyMessages: bestDailyMessages,
      AchievementMetric.bestStreak: bestStreak,
      AchievementMetric.imageMessages: imageMessages,
      AchievementMetric.voiceMessages: voiceMessages,
      AchievementMetric.voiceSeconds: voiceSeconds,
      AchievementMetric.stickerMessages: stickerMessages,
      AchievementMetric.mediaKinds: mediaKinds.length,
      AchievementMetric.morningMessages: morningMessages,
      AchievementMetric.nightMessages: nightMessages,
      AchievementMetric.morningMutualDays: morningMutualDays,
      AchievementMetric.nightMutualDays: nightMutualDays,
      AchievementMetric.proactiveMessages: proactiveMessages,
      AchievementMetric.favoriteMessages: max(0, favoriteCount),
      AchievementMetric.relationshipDays: relationshipDays,
      AchievementMetric.textCharacters: textCharacters,
    });
  }
}

class ChatAchievement {
  const ChatAchievement({
    required this.id,
    required this.chapter,
    required this.title,
    required this.description,
    required this.metric,
    required this.target,
    required this.icon,
    required this.rarity,
    this.hidden = false,
  });

  final String id;
  final AchievementChapter chapter;
  final String title;
  final String description;
  final AchievementMetric metric;
  final int target;
  final IconData icon;
  final AchievementRarity rarity;
  final bool hidden;

  int current(RelationshipAchievementStats stats) => stats.value(metric);

  bool unlocked(RelationshipAchievementStats stats) => current(stats) >= target;

  double progress(RelationshipAchievementStats stats) =>
      (current(stats) / max(1, target)).clamp(0.0, 1.0);
}

class ChatAchievementCatalog {
  const ChatAchievementCatalog._();

  static const definitions = <ChatAchievement>[
    ChatAchievement(
      id: 'first_message',
      chapter: AchievementChapter.firstLight,
      title: '故事的第一页',
      description: '留下属于你们的第一条聊天记录',
      metric: AchievementMetric.totalMessages,
      target: 1,
      icon: Icons.auto_stories_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'first_reply',
      chapter: AchievementChapter.firstLight,
      title: '第一次回应',
      description: '完成第一次彼此回应',
      metric: AchievementMetric.replyTurns,
      target: 1,
      icon: Icons.forum_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'ten_messages',
      chapter: AchievementChapter.firstLight,
      title: '小小话匣',
      description: '共同留下 10 条消息',
      metric: AchievementMetric.totalMessages,
      target: 10,
      icon: Icons.chat_bubble_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'fifty_messages',
      chapter: AchievementChapter.firstLight,
      title: '聊到忘记时间',
      description: '共同留下 50 条消息',
      metric: AchievementMetric.totalMessages,
      target: 50,
      icon: Icons.hourglass_bottom_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'hundred_messages',
      chapter: AchievementChapter.firstLight,
      title: '百句故事',
      description: '共同留下 100 条消息',
      metric: AchievementMetric.totalMessages,
      target: 100,
      icon: Icons.menu_book_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'first_mutual_day',
      chapter: AchievementChapter.firstLight,
      title: '同一天的我们',
      description: '在同一天里彼此都留下消息',
      metric: AchievementMetric.mutualDays,
      target: 1,
      icon: Icons.wb_twilight_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'three_active_days',
      chapter: AchievementChapter.firstLight,
      title: '三日微光',
      description: '在 3 个不同日期聊过天',
      metric: AchievementMetric.activeDays,
      target: 3,
      icon: Icons.flare_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'seven_active_days',
      chapter: AchievementChapter.firstLight,
      title: '一周相识',
      description: '在 7 个不同日期聊过天',
      metric: AchievementMetric.activeDays,
      target: 7,
      icon: Icons.calendar_view_week_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'first_image',
      chapter: AchievementChapter.firstLight,
      title: '第一张日常',
      description: '分享第一张图片',
      metric: AchievementMetric.imageMessages,
      target: 1,
      icon: Icons.photo_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'first_voice',
      chapter: AchievementChapter.firstLight,
      title: '第一次听见你',
      description: '留下第一条语音消息',
      metric: AchievementMetric.voiceMessages,
      target: 1,
      icon: Icons.mic_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'first_sticker',
      chapter: AchievementChapter.firstLight,
      title: '会心一笑',
      description: '发送第一条表情消息',
      metric: AchievementMetric.stickerMessages,
      target: 1,
      icon: Icons.sentiment_very_satisfied_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'first_favorite',
      chapter: AchievementChapter.firstLight,
      title: '值得珍藏',
      description: '收藏第一条舍不得忘记的消息',
      metric: AchievementMetric.favoriteMessages,
      target: 1,
      icon: Icons.bookmark_rounded,
      rarity: AchievementRarity.secret,
      hidden: true,
    ),
    ChatAchievement(
      id: 'daily_200',
      chapter: AchievementChapter.dailyLife,
      title: '话题不断',
      description: '共同留下 200 条消息',
      metric: AchievementMetric.totalMessages,
      target: 200,
      icon: Icons.sms_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'daily_500',
      chapter: AchievementChapter.dailyLife,
      title: '半千絮语',
      description: '共同留下 500 条消息',
      metric: AchievementMetric.totalMessages,
      target: 500,
      icon: Icons.question_answer_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'daily_1000',
      chapter: AchievementChapter.dailyLife,
      title: '千言相伴',
      description: '共同留下 1000 条消息',
      metric: AchievementMetric.totalMessages,
      target: 1000,
      icon: Icons.mark_chat_read_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'daily_2000',
      chapter: AchievementChapter.dailyLife,
      title: '说不完的话',
      description: '共同留下 2000 条消息',
      metric: AchievementMetric.totalMessages,
      target: 2000,
      icon: Icons.all_inbox_rounded,
      rarity: AchievementRarity.epic,
    ),
    ChatAchievement(
      id: 'daily_10_days',
      chapter: AchievementChapter.dailyLife,
      title: '十日相伴',
      description: '在 10 个不同日期聊过天',
      metric: AchievementMetric.activeDays,
      target: 10,
      icon: Icons.calendar_month_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'daily_30_days',
      chapter: AchievementChapter.dailyLife,
      title: '月光作伴',
      description: '在 30 个不同日期聊过天',
      metric: AchievementMetric.activeDays,
      target: 30,
      icon: Icons.nights_stay_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'daily_60_days',
      chapter: AchievementChapter.dailyLife,
      title: '两月日常',
      description: '在 60 个不同日期聊过天',
      metric: AchievementMetric.activeDays,
      target: 60,
      icon: Icons.date_range_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'daily_100_days',
      chapter: AchievementChapter.dailyLife,
      title: '百日成诗',
      description: '在 100 个不同日期聊过天',
      metric: AchievementMetric.activeDays,
      target: 100,
      icon: Icons.edit_calendar_rounded,
      rarity: AchievementRarity.epic,
    ),
    ChatAchievement(
      id: 'streak_3',
      chapter: AchievementChapter.dailyLife,
      title: '连续三天',
      description: '连续 3 天留下聊天记录',
      metric: AchievementMetric.bestStreak,
      target: 3,
      icon: Icons.local_fire_department_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'streak_7',
      chapter: AchievementChapter.dailyLife,
      title: '一周不断线',
      description: '连续 7 天留下聊天记录',
      metric: AchievementMetric.bestStreak,
      target: 7,
      icon: Icons.bolt_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'streak_14',
      chapter: AchievementChapter.dailyLife,
      title: '两周如一',
      description: '连续 14 天留下聊天记录',
      metric: AchievementMetric.bestStreak,
      target: 14,
      icon: Icons.stars_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'streak_30',
      chapter: AchievementChapter.dailyLife,
      title: '日常奇迹',
      description: '连续 30 天留下聊天记录',
      metric: AchievementMetric.bestStreak,
      target: 30,
      icon: Icons.workspace_premium_rounded,
      rarity: AchievementRarity.secret,
      hidden: true,
    ),
    ChatAchievement(
      id: 'turns_10',
      chapter: AchievementChapter.chemistry,
      title: '一来一回',
      description: '完成 10 次彼此回应',
      metric: AchievementMetric.replyTurns,
      target: 10,
      icon: Icons.swap_horiz_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'turns_50',
      chapter: AchievementChapter.chemistry,
      title: '接住话题',
      description: '完成 50 次彼此回应',
      metric: AchievementMetric.replyTurns,
      target: 50,
      icon: Icons.sync_alt_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'turns_200',
      chapter: AchievementChapter.chemistry,
      title: '心有来回',
      description: '完成 200 次彼此回应',
      metric: AchievementMetric.replyTurns,
      target: 200,
      icon: Icons.connect_without_contact_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'quick_5',
      chapter: AchievementChapter.chemistry,
      title: '立刻想回你',
      description: '在 5 分钟内彼此回应 5 次',
      metric: AchievementMetric.quickReplies,
      target: 5,
      icon: Icons.flash_on_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'quick_30',
      chapter: AchievementChapter.chemistry,
      title: '默契连线',
      description: '在 5 分钟内彼此回应 30 次',
      metric: AchievementMetric.quickReplies,
      target: 30,
      icon: Icons.electric_bolt_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'quick_100',
      chapter: AchievementChapter.chemistry,
      title: '心跳同频',
      description: '在 5 分钟内彼此回应 100 次',
      metric: AchievementMetric.quickReplies,
      target: 100,
      icon: Icons.monitor_heart_rounded,
      rarity: AchievementRarity.epic,
    ),
    ChatAchievement(
      id: 'mutual_3',
      chapter: AchievementChapter.chemistry,
      title: '三次双向奔赴',
      description: '在 3 个日期里彼此都留下消息',
      metric: AchievementMetric.mutualDays,
      target: 3,
      icon: Icons.compare_arrows_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'mutual_10',
      chapter: AchievementChapter.chemistry,
      title: '十日有回应',
      description: '在 10 个日期里彼此都留下消息',
      metric: AchievementMetric.mutualDays,
      target: 10,
      icon: Icons.handshake_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'mutual_30',
      chapter: AchievementChapter.chemistry,
      title: '月度默契',
      description: '在 30 个日期里彼此都留下消息',
      metric: AchievementMetric.mutualDays,
      target: 30,
      icon: Icons.favorite_border_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'sessions_10',
      chapter: AchievementChapter.chemistry,
      title: '十次约会',
      description: '开启 10 段独立聊天时光',
      metric: AchievementMetric.conversationSessions,
      target: 10,
      icon: Icons.meeting_room_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'sessions_50',
      chapter: AchievementChapter.chemistry,
      title: '总有话说',
      description: '开启 50 段独立聊天时光',
      metric: AchievementMetric.conversationSessions,
      target: 50,
      icon: Icons.weekend_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'long_sessions_5',
      chapter: AchievementChapter.chemistry,
      title: '舍不得结束',
      description: '拥有 5 段超过 20 条消息的长聊天',
      metric: AchievementMetric.longSessions,
      target: 5,
      icon: Icons.timelapse_rounded,
      rarity: AchievementRarity.secret,
      hidden: true,
    ),
    ChatAchievement(
      id: 'images_5',
      chapter: AchievementChapter.sharing,
      title: '生活切片',
      description: '分享 5 张图片',
      metric: AchievementMetric.imageMessages,
      target: 5,
      icon: Icons.collections_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'images_20',
      chapter: AchievementChapter.sharing,
      title: '二十帧日常',
      description: '分享 20 张图片',
      metric: AchievementMetric.imageMessages,
      target: 20,
      icon: Icons.photo_library_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'images_50',
      chapter: AchievementChapter.sharing,
      title: '私人相册',
      description: '分享 50 张图片',
      metric: AchievementMetric.imageMessages,
      target: 50,
      icon: Icons.collections_bookmark_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'images_100',
      chapter: AchievementChapter.sharing,
      title: '百张生活',
      description: '分享 100 张图片',
      metric: AchievementMetric.imageMessages,
      target: 100,
      icon: Icons.auto_awesome_mosaic_rounded,
      rarity: AchievementRarity.epic,
    ),
    ChatAchievement(
      id: 'voices_5',
      chapter: AchievementChapter.sharing,
      title: '声音初见',
      description: '留下 5 条语音消息',
      metric: AchievementMetric.voiceMessages,
      target: 5,
      icon: Icons.keyboard_voice_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'voices_20',
      chapter: AchievementChapter.sharing,
      title: '语音里的拥抱',
      description: '留下 20 条语音消息',
      metric: AchievementMetric.voiceMessages,
      target: 20,
      icon: Icons.record_voice_over_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'voices_100',
      chapter: AchievementChapter.sharing,
      title: '熟悉的声音',
      description: '留下 100 条语音消息',
      metric: AchievementMetric.voiceMessages,
      target: 100,
      icon: Icons.graphic_eq_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'voice_10_minutes',
      chapter: AchievementChapter.sharing,
      title: '十分钟陪伴',
      description: '累计分享 10 分钟语音',
      metric: AchievementMetric.voiceSeconds,
      target: 600,
      icon: Icons.av_timer_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'voice_hour',
      chapter: AchievementChapter.sharing,
      title: '一小时耳语',
      description: '累计分享 1 小时语音',
      metric: AchievementMetric.voiceSeconds,
      target: 3600,
      icon: Icons.headphones_rounded,
      rarity: AchievementRarity.epic,
    ),
    ChatAchievement(
      id: 'stickers_10',
      chapter: AchievementChapter.sharing,
      title: '表情会说话',
      description: '发送 10 条表情消息',
      metric: AchievementMetric.stickerMessages,
      target: 10,
      icon: Icons.face_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'stickers_50',
      chapter: AchievementChapter.sharing,
      title: '不用说也懂',
      description: '发送 50 条表情消息',
      metric: AchievementMetric.stickerMessages,
      target: 50,
      icon: Icons.emoji_emotions_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'media_kinds',
      chapter: AchievementChapter.sharing,
      title: '多彩表达',
      description: '使用 4 种不同的消息形式',
      metric: AchievementMetric.mediaKinds,
      target: 4,
      icon: Icons.palette_rounded,
      rarity: AchievementRarity.secret,
      hidden: true,
    ),
    ChatAchievement(
      id: 'night_10',
      chapter: AchievementChapter.care,
      title: '夜色里的消息',
      description: '在夜晚时段共同留下 10 条消息',
      metric: AchievementMetric.nightMessages,
      target: 10,
      icon: Icons.dark_mode_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'night_30',
      chapter: AchievementChapter.care,
      title: '晚安守护者',
      description: '在夜晚时段共同留下 30 条消息',
      metric: AchievementMetric.nightMessages,
      target: 30,
      icon: Icons.bedtime_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'night_100',
      chapter: AchievementChapter.care,
      title: '星光不眠',
      description: '在夜晚时段共同留下 100 条消息',
      metric: AchievementMetric.nightMessages,
      target: 100,
      icon: Icons.nightlight_round,
      rarity: AchievementRarity.epic,
    ),
    ChatAchievement(
      id: 'morning_10',
      chapter: AchievementChapter.care,
      title: '第一声早安',
      description: '在清晨时段共同留下 10 条消息',
      metric: AchievementMetric.morningMessages,
      target: 10,
      icon: Icons.wb_sunny_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'morning_30',
      chapter: AchievementChapter.care,
      title: '晨光问候',
      description: '在清晨时段共同留下 30 条消息',
      metric: AchievementMetric.morningMessages,
      target: 30,
      icon: Icons.wb_twilight_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'night_mutual_3',
      chapter: AchievementChapter.care,
      title: '三个相伴夜晚',
      description: '在 3 个夜晚彼此都留下消息',
      metric: AchievementMetric.nightMutualDays,
      target: 3,
      icon: Icons.hotel_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'night_mutual_10',
      chapter: AchievementChapter.care,
      title: '十夜相守',
      description: '在 10 个夜晚彼此都留下消息',
      metric: AchievementMetric.nightMutualDays,
      target: 10,
      icon: Icons.shield_moon_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'proactive_3',
      chapter: AchievementChapter.care,
      title: '她先想起你',
      description: '收到 3 次相隔一段时间后的主动消息',
      metric: AchievementMetric.proactiveMessages,
      target: 3,
      icon: Icons.mark_unread_chat_alt_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'proactive_20',
      chapter: AchievementChapter.care,
      title: '总会惦记',
      description: '收到 20 次相隔一段时间后的主动消息',
      metric: AchievementMetric.proactiveMessages,
      target: 20,
      icon: Icons.notifications_active_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'favorites_5',
      chapter: AchievementChapter.care,
      title: '五枚珍藏',
      description: '收藏 5 条重要消息',
      metric: AchievementMetric.favoriteMessages,
      target: 5,
      icon: Icons.collections_bookmark_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'favorites_20',
      chapter: AchievementChapter.care,
      title: '舍不得忘记',
      description: '收藏 20 条重要消息',
      metric: AchievementMetric.favoriteMessages,
      target: 20,
      icon: Icons.favorite_rounded,
      rarity: AchievementRarity.epic,
    ),
    ChatAchievement(
      id: 'busy_day_50',
      chapter: AchievementChapter.care,
      title: '一日长谈',
      description: '在同一天共同留下 50 条消息',
      metric: AchievementMetric.bestDailyMessages,
      target: 50,
      icon: Icons.today_rounded,
      rarity: AchievementRarity.secret,
      hidden: true,
    ),
    ChatAchievement(
      id: 'days_7',
      chapter: AchievementChapter.forever,
      title: '相伴一周',
      description: '从第一条记录起走过 7 天',
      metric: AchievementMetric.relationshipDays,
      target: 7,
      icon: Icons.calendar_view_week_rounded,
      rarity: AchievementRarity.common,
    ),
    ChatAchievement(
      id: 'days_30',
      chapter: AchievementChapter.forever,
      title: '相伴一月',
      description: '从第一条记录起走过 30 天',
      metric: AchievementMetric.relationshipDays,
      target: 30,
      icon: Icons.calendar_month_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'days_100',
      chapter: AchievementChapter.forever,
      title: '百日同行',
      description: '从第一条记录起走过 100 天',
      metric: AchievementMetric.relationshipDays,
      target: 100,
      icon: Icons.celebration_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'days_365',
      chapter: AchievementChapter.forever,
      title: '一周年',
      description: '从第一条记录起走过 365 天',
      metric: AchievementMetric.relationshipDays,
      target: 365,
      icon: Icons.cake_rounded,
      rarity: AchievementRarity.legendary,
    ),
    ChatAchievement(
      id: 'active_50',
      chapter: AchievementChapter.forever,
      title: '五十个有你的日子',
      description: '在 50 个不同日期聊过天',
      metric: AchievementMetric.activeDays,
      target: 50,
      icon: Icons.event_available_rounded,
      rarity: AchievementRarity.refined,
    ),
    ChatAchievement(
      id: 'active_100',
      chapter: AchievementChapter.forever,
      title: '百日有声',
      description: '在 100 个不同日期聊过天',
      metric: AchievementMetric.activeDays,
      target: 100,
      icon: Icons.event_note_rounded,
      rarity: AchievementRarity.precious,
    ),
    ChatAchievement(
      id: 'active_365',
      chapter: AchievementChapter.forever,
      title: '岁岁有回应',
      description: '在 365 个不同日期聊过天',
      metric: AchievementMetric.activeDays,
      target: 365,
      icon: Icons.event_repeat_rounded,
      rarity: AchievementRarity.legendary,
    ),
    ChatAchievement(
      id: 'messages_5000',
      chapter: AchievementChapter.forever,
      title: '五千句相伴',
      description: '共同留下 5000 条消息',
      metric: AchievementMetric.totalMessages,
      target: 5000,
      icon: Icons.library_books_rounded,
      rarity: AchievementRarity.epic,
    ),
    ChatAchievement(
      id: 'messages_10000',
      chapter: AchievementChapter.forever,
      title: '万语长情',
      description: '共同留下 10000 条消息',
      metric: AchievementMetric.totalMessages,
      target: 10000,
      icon: Icons.auto_stories_rounded,
      rarity: AchievementRarity.legendary,
    ),
    ChatAchievement(
      id: 'chars_100k',
      chapter: AchievementChapter.forever,
      title: '十万字情书',
      description: '聊天文字累计达到 10 万字',
      metric: AchievementMetric.textCharacters,
      target: 100000,
      icon: Icons.history_edu_rounded,
      rarity: AchievementRarity.epic,
    ),
    ChatAchievement(
      id: 'chars_500k',
      chapter: AchievementChapter.forever,
      title: '半百万字的我们',
      description: '聊天文字累计达到 50 万字',
      metric: AchievementMetric.textCharacters,
      target: 500000,
      icon: Icons.menu_book_rounded,
      rarity: AchievementRarity.legendary,
    ),
    ChatAchievement(
      id: 'mutual_100',
      chapter: AchievementChapter.forever,
      title: '双向的一百天',
      description: '在 100 个日期里彼此都留下消息',
      metric: AchievementMetric.mutualDays,
      target: 100,
      icon: Icons.volunteer_activism_rounded,
      rarity: AchievementRarity.secret,
      hidden: true,
    ),
  ];
}
