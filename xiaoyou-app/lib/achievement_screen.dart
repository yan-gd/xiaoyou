import 'dart:math';
import 'dart:ui';

import 'package:flutter/material.dart';

import 'achievement_models.dart';
import 'chat_models.dart';

const _achievementInk = Color(0xff382932);
const _achievementMuted = Color(0xff8f7a86);
const _achievementRose = Color(0xffa8517e);
const _achievementRoseDark = Color(0xff64354f);
const _achievementCanvas = Color(0xfffffbfd);
const _storybookBackground = 'assets/achievements/storybook-background.png';
const _achievementJewelFrame = 'assets/achievements/jewel-frame.png';
const _achievementLevelMedallion = 'assets/achievements/level-medallion.png';
const _achievementTreasureBox = 'assets/achievements/treasure-box.png';

class ChatAchievementScreen extends StatefulWidget {
  const ChatAchievementScreen({
    super.key,
    required this.messages,
    required this.favoriteCount,
  });

  final List<ChatMessage> messages;
  final int favoriteCount;

  @override
  State<ChatAchievementScreen> createState() => _ChatAchievementScreenState();
}

class _ChatAchievementScreenState extends State<ChatAchievementScreen> {
  late final RelationshipAchievementStats _stats =
      RelationshipAchievementStats.fromMessages(
    widget.messages,
    favoriteCount: widget.favoriteCount,
  );
  AchievementChapter _chapter = AchievementChapter.chemistry;
  ChatAchievement? _selected;

  List<ChatAchievement> get _chapterAchievements =>
      ChatAchievementCatalog.definitions
          .where((achievement) => achievement.chapter == _chapter)
          .toList(growable: false);

  int get _unlockedCount => ChatAchievementCatalog.definitions
      .where((achievement) => achievement.unlocked(_stats))
      .length;

  @override
  void initState() {
    super.initState();
    _selected = _bestStartingAchievement(_chapterAchievements);
  }

  ChatAchievement _bestStartingAchievement(
    List<ChatAchievement> achievements,
  ) {
    return achievements.firstWhere(
      (achievement) => !achievement.hidden && !achievement.unlocked(_stats),
      orElse: () => achievements.last,
    );
  }

  void _selectChapter(AchievementChapter chapter) {
    if (chapter == _chapter) {
      return;
    }
    setState(() {
      _chapter = chapter;
      _selected = _bestStartingAchievement(
        ChatAchievementCatalog.definitions
            .where((achievement) => achievement.chapter == chapter)
            .toList(growable: false),
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    final chapterAchievements = _chapterAchievements;
    final selected = _selected ?? chapterAchievements.first;
    return Scaffold(
      backgroundColor: _achievementCanvas,
      body: Stack(
        children: [
          const Positioned.fill(child: _AchievementBackground()),
          SafeArea(
            child: Column(
              children: [
                _AchievementAppBar(
                  onBack: () => Navigator.maybePop(context),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
                  child: _AchievementSummary(
                    unlocked: _unlockedCount,
                    total: ChatAchievementCatalog.definitions.length,
                  ),
                ),
                Expanded(
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      _ChapterRail(
                        selected: _chapter,
                        stats: _stats,
                        onSelected: _selectChapter,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: _AchievementChapterPane(
                          chapter: _chapter,
                          achievements: chapterAchievements,
                          selected: selected,
                          stats: _stats,
                          onSelected: (achievement) {
                            setState(() => _selected = achievement);
                          },
                        ),
                      ),
                      const SizedBox(width: 12),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _AchievementAppBar extends StatelessWidget {
  const _AchievementAppBar({required this.onBack});

  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 62,
      child: Row(
        children: [
          const SizedBox(width: 10),
          _GlassIconButton(
            icon: Icons.arrow_back_ios_new_rounded,
            onTap: onBack,
          ),
          const Expanded(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  '我们的成就册',
                  style: TextStyle(
                    color: _achievementInk,
                    fontFamily: 'serif',
                    fontSize: 21,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 3.2,
                  ),
                ),
                SizedBox(height: 2),
                Text(
                  '每一次聊天，都在写下新的章节',
                  style: TextStyle(
                    color: _achievementMuted,
                    fontSize: 10.5,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: 46),
        ],
      ),
    );
  }
}

class _GlassIconButton extends StatelessWidget {
  const _GlassIconButton({
    required this.icon,
    required this.onTap,
  });

  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white.withValues(alpha: 0.68),
      shape: const CircleBorder(),
      child: InkWell(
        onTap: onTap,
        customBorder: const CircleBorder(),
        child: Container(
          width: 42,
          height: 42,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            border: Border.all(color: const Color(0xcaffffff)),
            boxShadow: const [
              BoxShadow(
                color: Color(0x145b3148),
                blurRadius: 14,
                offset: Offset(0, 5),
              ),
            ],
          ),
          child: Icon(icon, color: _achievementRoseDark, size: 20),
        ),
      ),
    );
  }
}

class _AchievementSummary extends StatelessWidget {
  const _AchievementSummary({
    required this.unlocked,
    required this.total,
  });

  final int unlocked;
  final int total;

  @override
  Widget build(BuildContext context) {
    final progress = unlocked / max(1, total);
    final level = max(1, min(12, 1 + unlocked ~/ 6));
    final levelName = switch (level) {
      <= 2 => '初识旅人',
      <= 4 => '相伴旅人',
      <= 6 => '默契收藏家',
      <= 8 => '心动守护者',
      <= 10 => '长情记录者',
      _ => '永恒同行者',
    };
    return _FlowingRibbonFrame(
      active: true,
      radius: 28,
      strength: 0.72,
      child: Container(
        padding: const EdgeInsets.fromLTRB(17, 16, 17, 15),
        decoration: BoxDecoration(
          gradient: const LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [
              Color(0xe8ffffff),
              Color(0xe8fff5fa),
              Color(0xeaf1ecfa),
            ],
          ),
          borderRadius: BorderRadius.circular(25),
          boxShadow: const [
            BoxShadow(
              color: Color(0x1f6d405a),
              blurRadius: 22,
              offset: Offset(0, 8),
            ),
          ],
        ),
        child: Row(
          children: [
            _RelationshipLevelMedallion(level: level),
            const SizedBox(width: 9),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '$levelName · Lv.$level',
                    style: const TextStyle(
                      color: _achievementInk,
                      fontFamily: 'serif',
                      fontSize: 15,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.8,
                    ),
                  ),
                  const SizedBox(height: 7),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text(
                        '$unlocked',
                        style: const TextStyle(
                          color: _achievementRoseDark,
                          fontSize: 30,
                          height: 1,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.only(left: 5, bottom: 2),
                        child: Text(
                          '/ $total 已解锁',
                          style: const TextStyle(
                            color: _achievementMuted,
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 9),
                  _LuminousProgress(value: progress, height: 7),
                ],
              ),
            ),
            const SizedBox(width: 10),
            Text(
              '${(progress * 100).round()}%',
              style: const TextStyle(
                color: _achievementRose,
                fontSize: 12,
                fontWeight: FontWeight.w800,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _RelationshipLevelMedallion extends StatelessWidget {
  const _RelationshipLevelMedallion({required this.level});

  final int level;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 82,
      height: 94,
      child: Stack(
        alignment: Alignment.center,
        children: [
          Positioned.fill(
            child: Image.asset(
              _achievementLevelMedallion,
              fit: BoxFit.contain,
              filterQuality: FilterQuality.high,
            ),
          ),
          Positioned(
            bottom: 9,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(
                gradient: const LinearGradient(
                  colors: [Color(0xff73435e), Color(0xff9a6a93)],
                ),
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: const Color(0xcaffffff)),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x3d65364f),
                    blurRadius: 7,
                  ),
                ],
              ),
              child: Text(
                '$level',
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 9,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ChapterRail extends StatelessWidget {
  const _ChapterRail({
    required this.selected,
    required this.stats,
    required this.onSelected,
  });

  final AchievementChapter selected;
  final RelationshipAchievementStats stats;
  final ValueChanged<AchievementChapter> onSelected;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 92,
      child: ListView.separated(
        primary: false,
        padding: const EdgeInsets.fromLTRB(10, 6, 0, 24),
        physics: const BouncingScrollPhysics(),
        itemCount: AchievementChapter.values.length,
        separatorBuilder: (_, __) => const SizedBox(height: 9),
        itemBuilder: (context, index) {
          final chapter = AchievementChapter.values[index];
          final achievements = ChatAchievementCatalog.definitions
              .where((achievement) => achievement.chapter == chapter)
              .toList(growable: false);
          final unlocked = achievements
              .where((achievement) => achievement.unlocked(stats))
              .length;
          return _ChapterButton(
            chapter: chapter,
            selected: chapter == selected,
            unlocked: unlocked,
            total: achievements.length,
            onTap: () => onSelected(chapter),
          );
        },
      ),
    );
  }
}

class _ChapterButton extends StatelessWidget {
  const _ChapterButton({
    required this.chapter,
    required this.selected,
    required this.unlocked,
    required this.total,
    required this.onTap,
  });

  final AchievementChapter chapter;
  final bool selected;
  final int unlocked;
  final int total;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final content = Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(22),
        child: Container(
          height: 114,
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 8),
          decoration: BoxDecoration(
            gradient: selected
                ? const LinearGradient(
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                    colors: [Color(0xffffedf6), Color(0xffeee5fa)],
                  )
                : const LinearGradient(
                    colors: [Color(0xd9ffffff), Color(0xcffcf6f9)],
                  ),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(
              color:
                  selected ? const Color(0x55b76690) : const Color(0xb8ffffff),
            ),
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              _MiniChapterMedallion(
                icon: chapter.icon,
                selected: selected,
              ),
              const SizedBox(height: 6),
              Text(
                chapter.title,
                maxLines: 1,
                style: TextStyle(
                  color: selected ? _achievementRoseDark : _achievementInk,
                  fontFamily: 'serif',
                  fontSize: 11,
                  height: 1.05,
                  fontWeight: selected ? FontWeight.w700 : FontWeight.w600,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                '$unlocked / $total',
                style: const TextStyle(
                  color: _achievementMuted,
                  fontSize: 8.5,
                  height: 1,
                ),
              ),
            ],
          ),
        ),
      ),
    );
    return selected
        ? _FlowingRibbonFrame(
            active: true,
            radius: 23,
            strength: 0.85,
            child: content,
          )
        : content;
  }
}

class _MiniChapterMedallion extends StatelessWidget {
  const _MiniChapterMedallion({
    required this.icon,
    required this.selected,
  });

  final IconData icon;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 48,
      height: 53,
      child: Stack(
        alignment: Alignment.center,
        children: [
          Container(
            width: 34,
            height: 42,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(18),
              gradient: selected
                  ? const RadialGradient(
                      colors: [Color(0xffffffff), Color(0xffffe5f4)],
                    )
                  : const RadialGradient(
                      colors: [Color(0xffffffff), Color(0xfff5edf2)],
                    ),
              boxShadow: selected
                  ? const [
                      BoxShadow(
                        color: Color(0x4a9a75c7),
                        blurRadius: 12,
                      ),
                    ]
                  : null,
            ),
            child: Icon(
              icon,
              color: selected ? _achievementRose : const Color(0xffa996a0),
              size: 19,
            ),
          ),
          Positioned.fill(
            child: Image.asset(
              _achievementJewelFrame,
              fit: BoxFit.contain,
              filterQuality: FilterQuality.high,
            ),
          ),
        ],
      ),
    );
  }
}

class _AchievementChapterPane extends StatelessWidget {
  const _AchievementChapterPane({
    required this.chapter,
    required this.achievements,
    required this.selected,
    required this.stats,
    required this.onSelected,
  });

  final AchievementChapter chapter;
  final List<ChatAchievement> achievements;
  final ChatAchievement selected;
  final RelationshipAchievementStats stats;
  final ValueChanged<ChatAchievement> onSelected;

  @override
  Widget build(BuildContext context) {
    final unlocked =
        achievements.where((achievement) => achievement.unlocked(stats)).length;
    return ClipRRect(
      borderRadius: const BorderRadius.vertical(top: Radius.circular(30)),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
        child: DecoratedBox(
          decoration: BoxDecoration(
            gradient: const LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [Color(0xeaffffff), Color(0xeafdf4f9)],
            ),
            borderRadius: const BorderRadius.vertical(top: Radius.circular(30)),
            border: Border.all(color: const Color(0xbfffffff)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(17, 13, 8, 7),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            chapter.title,
                            style: const TextStyle(
                              color: _achievementInk,
                              fontFamily: 'serif',
                              fontSize: 20,
                              fontWeight: FontWeight.w700,
                              letterSpacing: 1.1,
                            ),
                          ),
                          const SizedBox(height: 3),
                          Text(
                            chapter.subtitle,
                            style: const TextStyle(
                              color: _achievementMuted,
                              fontSize: 10.5,
                            ),
                          ),
                        ],
                      ),
                    ),
                    SizedBox(
                      width: 83,
                      height: 63,
                      child: Stack(
                        clipBehavior: Clip.none,
                        alignment: Alignment.center,
                        children: [
                          Positioned.fill(
                            child: Image.asset(
                              _achievementTreasureBox,
                              fit: BoxFit.contain,
                              filterQuality: FilterQuality.high,
                            ),
                          ),
                          Positioned(
                            right: 0,
                            bottom: 0,
                            child: Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 7,
                                vertical: 3,
                              ),
                              decoration: BoxDecoration(
                                color: const Color(0xeef8edf6),
                                borderRadius: BorderRadius.circular(12),
                                border:
                                    Border.all(color: const Color(0xb8ffffff)),
                              ),
                              child: Text(
                                '$unlocked / ${achievements.length}',
                                style: const TextStyle(
                                  color: _achievementRoseDark,
                                  fontSize: 9,
                                  fontWeight: FontWeight.w800,
                                ),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 17),
                child: _LuminousProgress(
                  value: unlocked / max(1, achievements.length),
                  height: 4,
                ),
              ),
              const SizedBox(height: 11),
              Expanded(
                child: ListView.separated(
                  key: ValueKey(chapter),
                  primary: false,
                  padding: const EdgeInsets.fromLTRB(12, 0, 12, 28),
                  physics: const BouncingScrollPhysics(),
                  itemCount: achievements.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 9),
                  itemBuilder: (context, index) {
                    final achievement = achievements[index];
                    return _AchievementTile(
                      achievement: achievement,
                      stats: stats,
                      selected: achievement.id == selected.id,
                      onTap: () => onSelected(achievement),
                    );
                  },
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _AchievementTile extends StatelessWidget {
  const _AchievementTile({
    required this.achievement,
    required this.stats,
    required this.selected,
    required this.onTap,
  });

  final ChatAchievement achievement;
  final RelationshipAchievementStats stats;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final unlocked = achievement.unlocked(stats);
    final current = achievement.current(stats);
    final progress = achievement.progress(stats);
    final concealed = achievement.hidden && !unlocked;
    final title = concealed ? '???' : achievement.title;
    final description = concealed ? '达成条件尚未揭晓' : achievement.description;
    final tile = Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(23),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 360),
          curve: Curves.easeOutCubic,
          padding: EdgeInsets.fromLTRB(10, 10, 10, selected ? 12 : 10),
          decoration: BoxDecoration(
            gradient: selected
                ? const LinearGradient(
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                    colors: [Color(0xffffffff), Color(0xfffff3f9)],
                  )
                : LinearGradient(
                    colors: unlocked
                        ? const [Color(0xf5ffffff), Color(0xeffdf8fb)]
                        : const [Color(0xeef8f4f6), Color(0xeef3eef1)],
                  ),
            borderRadius: BorderRadius.circular(21),
            border: Border.all(
              color:
                  unlocked ? const Color(0x88ffffff) : const Color(0x55bfaeb6),
            ),
            boxShadow: selected
                ? const [
                    BoxShadow(
                      color: Color(0x245f354c),
                      blurRadius: 18,
                      offset: Offset(0, 8),
                    ),
                  ]
                : null,
          ),
          child: Column(
            children: [
              Row(
                children: [
                  _AchievementJewel(
                    icon: concealed ? Icons.lock_rounded : achievement.icon,
                    rarity: achievement.rarity,
                    unlocked: unlocked,
                    selected: selected,
                  ),
                  const SizedBox(width: 11),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Expanded(
                              child: Text(
                                title,
                                style: TextStyle(
                                  color: concealed
                                      ? const Color(0xffa79aa0)
                                      : _achievementInk,
                                  fontSize: 14,
                                  fontWeight: FontWeight.w800,
                                ),
                              ),
                            ),
                            if (unlocked)
                              const Icon(
                                Icons.check_circle_rounded,
                                color: Color(0xffb36a91),
                                size: 18,
                              )
                            else if (!concealed)
                              Text(
                                '$current / ${achievement.target}',
                                style: const TextStyle(
                                  color: _achievementRose,
                                  fontSize: 10,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                          ],
                        ),
                        const SizedBox(height: 4),
                        Text(
                          description,
                          maxLines: selected ? 2 : 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: _achievementMuted,
                            fontSize: 10.5,
                            height: 1.35,
                          ),
                        ),
                        if (!concealed) ...[
                          const SizedBox(height: 7),
                          _LuminousProgress(
                            value: progress,
                            height: selected ? 5 : 3.5,
                            muted: !unlocked && current == 0,
                          ),
                        ],
                      ],
                    ),
                  ),
                ],
              ),
              AnimatedSize(
                duration: const Duration(milliseconds: 340),
                curve: Curves.easeOutCubic,
                child: selected
                    ? _AchievementDetail(
                        achievement: achievement,
                        current: current,
                        unlocked: unlocked,
                        concealed: concealed,
                      )
                    : const SizedBox.shrink(),
              ),
            ],
          ),
        ),
      ),
    );
    return selected
        ? _FlowingRibbonFrame(
            active: true,
            radius: 25,
            strength: 1,
            child: tile,
          )
        : tile;
  }
}

class _AchievementDetail extends StatelessWidget {
  const _AchievementDetail({
    required this.achievement,
    required this.current,
    required this.unlocked,
    required this.concealed,
  });

  final ChatAchievement achievement;
  final int current;
  final bool unlocked;
  final bool concealed;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(2, 12, 2, 0),
      child: Column(
        children: [
          Container(
            height: 1,
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                colors: [
                  Color(0x00a8517e),
                  Color(0x55a8517e),
                  Color(0x00a8517e),
                ],
              ),
            ),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              _RarityBadge(rarity: achievement.rarity),
              const Spacer(),
              Text(
                concealed
                    ? '继续相伴，等待它出现'
                    : unlocked
                        ? '已经写进你们的故事'
                        : '还差 ${max(0, achievement.target - current)}',
                style: const TextStyle(
                  color: _achievementMuted,
                  fontSize: 9.5,
                ),
              ),
            ],
          ),
          const SizedBox(height: 9),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                colors: [Color(0xfffff7fb), Color(0xfff4eef8)],
              ),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: const Color(0xaaffffff)),
            ),
            child: Row(
              children: [
                Icon(
                  unlocked
                      ? Icons.workspace_premium_rounded
                      : Icons.auto_awesome_rounded,
                  color: unlocked
                      ? const Color(0xffb36c92)
                      : const Color(0xffaa9caf),
                  size: 17,
                ),
                const SizedBox(width: 7),
                Expanded(
                  child: Text(
                    unlocked
                        ? '专属徽章已点亮'
                        : concealed
                            ? '秘密成就仍被雾光包裹'
                            : '达成后点亮专属成就徽章',
                    style: const TextStyle(
                      color: _achievementRoseDark,
                      fontSize: 10,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
                  decoration: BoxDecoration(
                    color: unlocked
                        ? const Color(0xffefe1f1)
                        : const Color(0xffeee9ed),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Text(
                    unlocked ? '已解锁' : '进行中',
                    style: TextStyle(
                      color: unlocked
                          ? _achievementRoseDark
                          : const Color(0xff92838a),
                      fontSize: 9,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _RarityBadge extends StatelessWidget {
  const _RarityBadge({required this.rarity});

  final AchievementRarity rarity;

  @override
  Widget build(BuildContext context) {
    final colors = _rarityColors(rarity);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        gradient: LinearGradient(colors: colors),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0x9fffffff)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(
            Icons.diamond_rounded,
            color: Colors.white,
            size: 10,
          ),
          const SizedBox(width: 4),
          Text(
            rarity.label,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 9,
              fontWeight: FontWeight.w800,
            ),
          ),
        ],
      ),
    );
  }
}

class _AchievementJewel extends StatelessWidget {
  const _AchievementJewel({
    required this.icon,
    required this.rarity,
    required this.unlocked,
    required this.selected,
  });

  final IconData icon;
  final AchievementRarity rarity;
  final bool unlocked;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final rarityColors = _rarityColors(rarity);
    return AnimatedContainer(
      duration: const Duration(milliseconds: 320),
      width: selected ? 67 : 61,
      height: selected ? 76 : 69,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(28),
        boxShadow: unlocked
            ? [
                BoxShadow(
                  color: rarityColors.first.withValues(alpha: 0.28),
                  blurRadius: selected ? 22 : 13,
                  offset: const Offset(0, 6),
                ),
              ]
            : null,
      ),
      child: Stack(
        alignment: Alignment.center,
        children: [
          Container(
            width: selected ? 44 : 40,
            height: selected ? 56 : 50,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(24),
              gradient: unlocked
                  ? RadialGradient(
                      colors: [
                        const Color(0xffffffff),
                        rarityColors.first.withValues(alpha: 0.23),
                      ],
                    )
                  : const RadialGradient(
                      colors: [Color(0xfff1ecef), Color(0xffd9d0d5)],
                    ),
            ),
            child: ShaderMask(
              shaderCallback: (bounds) => LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: unlocked
                    ? rarityColors
                    : const [Color(0xffb1a4ab), Color(0xff93868c)],
              ).createShader(bounds),
              child: Icon(
                icon,
                color: Colors.white,
                size: selected ? 28 : 24,
              ),
            ),
          ),
          Positioned.fill(
            child: Opacity(
              opacity: unlocked ? 1 : 0.46,
              child: Image.asset(
                _achievementJewelFrame,
                fit: BoxFit.contain,
                filterQuality: FilterQuality.high,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _LuminousProgress extends StatelessWidget {
  const _LuminousProgress({
    required this.value,
    required this.height,
    this.muted = false,
  });

  final double value;
  final double height;
  final bool muted;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(99),
      child: SizedBox(
        height: height,
        child: Stack(
          children: [
            const Positioned.fill(
              child: ColoredBox(color: Color(0xffe9e0e5)),
            ),
            FractionallySizedBox(
              widthFactor: value.clamp(0.0, 1.0).toDouble(),
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: muted
                      ? const LinearGradient(
                          colors: [Color(0xffc9bdc3), Color(0xffb5a9af)],
                        )
                      : const LinearGradient(
                          colors: [
                            Color(0xffd57aa7),
                            Color(0xff9e78c7),
                            Color(0xffffcf91),
                          ],
                        ),
                  boxShadow: const [
                    BoxShadow(
                      color: Color(0x559f4f79),
                      blurRadius: 7,
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _FlowingRibbonFrame extends StatefulWidget {
  const _FlowingRibbonFrame({
    required this.child,
    required this.active,
    required this.radius,
    required this.strength,
  });

  final Widget child;
  final bool active;
  final double radius;
  final double strength;

  @override
  State<_FlowingRibbonFrame> createState() => _FlowingRibbonFrameState();
}

class _FlowingRibbonFrameState extends State<_FlowingRibbonFrame>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 3800),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.active) {
      return widget.child;
    }
    return RepaintBoundary(
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, child) => CustomPaint(
          painter: _FlowingRibbonPainter(
            progress: _controller.value,
            radius: widget.radius,
            strength: widget.strength,
          ),
          child: Padding(
            padding: const EdgeInsets.all(3),
            child: child,
          ),
        ),
        child: widget.child,
      ),
    );
  }
}

class _FlowingRibbonPainter extends CustomPainter {
  const _FlowingRibbonPainter({
    required this.progress,
    required this.radius,
    required this.strength,
  });

  final double progress;
  final double radius;
  final double strength;

  @override
  void paint(Canvas canvas, Size size) {
    final rect = (Offset.zero & size).deflate(2);
    final rrect = RRect.fromRectAndRadius(rect, Radius.circular(radius));
    final gradient = SweepGradient(
      transform: GradientRotation(progress * pi * 2),
      colors: const [
        Color(0xffffafd1),
        Color(0xffffe0a0),
        Color(0xffffffff),
        Color(0xffb699eb),
        Color(0xffffafd1),
      ],
      stops: const [0, 0.22, 0.44, 0.72, 1],
    ).createShader(rect);
    final glow = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 7 * strength
      ..shader = gradient
      ..maskFilter = MaskFilter.blur(BlurStyle.normal, 7 * strength);
    canvas.drawRRect(rrect, glow);
    final ribbon = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.7 + strength
      ..shader = gradient;
    canvas.drawRRect(rrect, ribbon);

    final path = Path()..addRRect(rrect);
    final metrics = path.computeMetrics().toList(growable: false);
    if (metrics.isEmpty) {
      return;
    }
    final metric = metrics.first;
    final distance = metric.length * progress;
    final tangent = metric.getTangentForOffset(distance);
    if (tangent == null) {
      return;
    }
    canvas.drawCircle(
      tangent.position,
      2.2 + strength,
      Paint()
        ..color = Colors.white
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 3),
    );
  }

  @override
  bool shouldRepaint(covariant _FlowingRibbonPainter oldDelegate) =>
      oldDelegate.progress != progress ||
      oldDelegate.radius != radius ||
      oldDelegate.strength != strength;
}

class _AchievementBackground extends StatefulWidget {
  const _AchievementBackground();

  @override
  State<_AchievementBackground> createState() => _AchievementBackgroundState();
}

class _AchievementBackgroundState extends State<_AchievementBackground>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 9000),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      fit: StackFit.expand,
      children: [
        Image.asset(
          _storybookBackground,
          fit: BoxFit.cover,
          alignment: Alignment.topCenter,
          filterQuality: FilterQuality.high,
        ),
        DecoratedBox(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [
                Colors.white.withValues(alpha: 0.14),
                const Color(0xfffff8fb).withValues(alpha: 0.05),
                const Color(0xfff7f0fa).withValues(alpha: 0.12),
              ],
            ),
          ),
        ),
        AnimatedBuilder(
          animation: _controller,
          builder: (context, child) => CustomPaint(
            foregroundPainter: _AchievementBackgroundPainter(_controller.value),
          ),
        ),
      ],
    );
  }
}

class _AchievementBackgroundPainter extends CustomPainter {
  const _AchievementBackgroundPainter(this.progress);

  final double progress;

  @override
  void paint(Canvas canvas, Size size) {
    const particles = [
      Offset(0.08, 0.18),
      Offset(0.76, 0.11),
      Offset(0.91, 0.32),
      Offset(0.22, 0.47),
      Offset(0.70, 0.61),
      Offset(0.12, 0.81),
      Offset(0.86, 0.89),
      Offset(0.48, 0.74),
    ];
    for (var index = 0; index < particles.length; index++) {
      final source = particles[index];
      final drift = sin(progress * pi * 2 + index * 0.91) * 7;
      final position = Offset(
        source.dx * size.width + drift,
        source.dy * size.height + cos(progress * pi * 2 + index) * 5,
      );
      final alpha = (0.16 + sin(progress * pi * 2 + index * 0.7).abs() * 0.22);
      canvas.drawCircle(
        position,
        1.3 + (index % 3) * 0.5,
        Paint()
          ..color = const Color(0xffb36f9a).withValues(alpha: alpha)
          ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 2),
      );
    }
  }

  @override
  bool shouldRepaint(covariant _AchievementBackgroundPainter oldDelegate) =>
      oldDelegate.progress != progress;
}

List<Color> _rarityColors(AchievementRarity rarity) => switch (rarity) {
      AchievementRarity.common => const [
          Color(0xffbdaeb5),
          Color(0xff8f8088),
        ],
      AchievementRarity.refined => const [
          Color(0xffdfa0bd),
          Color(0xffa76084),
        ],
      AchievementRarity.precious => const [
          Color(0xffb493dc),
          Color(0xff765ba8),
        ],
      AchievementRarity.epic => const [
          Color(0xffce82cf),
          Color(0xff8750a2),
        ],
      AchievementRarity.legendary => const [
          Color(0xffffcf83),
          Color(0xffca7d51),
        ],
      AchievementRarity.secret => const [
          Color(0xff70627f),
          Color(0xff382d45),
        ],
    };
