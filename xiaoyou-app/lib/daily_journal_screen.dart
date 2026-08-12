import 'package:flutter/material.dart';

import 'relationship_models.dart';
import 'xiaoyou_api.dart';
import 'theme_controller.dart';

const _journalInk = Color(0xff402d38);
const _journalMuted = Color(0xff9d8893);
const _journalRose = Color(0xffb65b88);

class DailyJournalScreen extends StatefulWidget {
  const DailyJournalScreen({
    super.key,
    required this.api,
    this.initialJournal,
  });

  final XiaoyouApi api;
  final DailyJournal? initialJournal;

  @override
  State<DailyJournalScreen> createState() => _DailyJournalScreenState();
}

class _DailyJournalScreenState extends State<DailyJournalScreen> {
  DailyJournal? _journal;
  final _summary = TextEditingController();
  final _tomorrow = TextEditingController();
  bool _loading = true;
  bool _saving = false;
  String _error = '';

  @override
  void initState() {
    super.initState();
    _journal = widget.initialJournal;
    if (_journal != null) {
      _adopt(_journal!);
      _loading = false;
    } else {
      _load();
    }
  }

  @override
  void dispose() {
    _summary.dispose();
    _tomorrow.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final journal = await widget.api.draftDailyJournal(DateTime.now());
      if (!mounted) {
        return;
      }
      setState(() {
        _journal = journal;
        _loading = false;
      });
      _adopt(journal);
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _error = '日记草稿暂时没有整理好，请稍后再试。';
        _loading = false;
      });
    }
  }

  void _adopt(DailyJournal journal) {
    _summary.text = journal.summary;
    _tomorrow.text = journal.tomorrowWish;
  }

  Future<void> _confirm() async {
    final journal = _journal;
    if (journal == null || _saving) {
      return;
    }
    setState(() => _saving = true);
    try {
      final saved = await widget.api.confirmDailyJournal(
        journal,
        summary: _summary.text.trim(),
        tomorrowWish: _tomorrow.text.trim(),
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _journal = saved;
        _saving = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('这一天已经收藏进你们的星河')),
      );
      await Future<void>.delayed(const Duration(milliseconds: 500));
      if (mounted) {
        Navigator.of(context).pop(saved.entry);
      }
    } catch (_) {
      if (mounted) {
        setState(() => _saving = false);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('保存失败，草稿仍然保留在这里')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final journal = _journal;
    return Scaffold(
      body: Stack(
        children: [
          const Positioned.fill(child: _JournalBackground()),
          SafeArea(
            child: Column(
              children: [
                _JournalAppBar(onBack: () => Navigator.of(context).pop()),
                Expanded(
                  child: AnimatedSwitcher(
                    duration: const Duration(milliseconds: 420),
                    child: _loading
                        ? const _JournalLoading()
                        : _error.isNotEmpty
                            ? _JournalError(message: _error, onRetry: _load)
                            : _JournalPage(
                                journal: journal!,
                                summary: _summary,
                                tomorrow: _tomorrow,
                                api: widget.api,
                                saving: _saving,
                                onConfirm: _confirm,
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

class _JournalAppBar extends StatelessWidget {
  const _JournalAppBar({required this.onBack});

  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 20, 8),
      child: Row(
        children: [
          IconButton.filledTonal(
            onPressed: onBack,
            icon: const Icon(Icons.arrow_back_ios_new_rounded, size: 19),
          ),
          const SizedBox(width: 12),
          const Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '我们今天',
                style: TextStyle(
                  color: _journalInk,
                  fontSize: 23,
                  fontWeight: FontWeight.w800,
                ),
              ),
              Text(
                '先确认，再让今天成为共同经历',
                style: TextStyle(color: _journalMuted, fontSize: 12),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _JournalPage extends StatelessWidget {
  const _JournalPage({
    required this.journal,
    required this.summary,
    required this.tomorrow,
    required this.api,
    required this.saving,
    required this.onConfirm,
  });

  final DailyJournal journal;
  final TextEditingController summary;
  final TextEditingController tomorrow;
  final XiaoyouApi api;
  final bool saving;
  final VoidCallback onConfirm;

  @override
  Widget build(BuildContext context) {
    final mediaId = journal.representativeMediaId;
    return ListView(
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 34),
      children: [
        _JournalPaper(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const _JournalSeal(),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          journal.entry.title,
                          style: const TextStyle(
                            color: _journalInk,
                            fontSize: 20,
                            fontWeight: FontWeight.w800,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          _prettyDate(journal.entry.eventTime),
                          style: const TextStyle(
                            color: _journalMuted,
                            fontSize: 12,
                          ),
                        ),
                      ],
                    ),
                  ),
                  _DraftBadge(confirmed: journal.confirmed),
                ],
              ),
              if (journal.moodChanges.isNotEmpty) ...[
                const SizedBox(height: 20),
                const Text(
                  '小悠的心情变化',
                  style: TextStyle(
                    color: _journalInk,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 9),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: journal.moodChanges
                      .map((mood) => _MoodPetal(label: mood))
                      .toList(),
                ),
              ],
              const SizedBox(height: 20),
              TextField(
                controller: summary,
                minLines: 4,
                maxLines: 8,
                decoration: const InputDecoration(
                  labelText: '今天聊过的事情',
                  hintText: '只保留真正发生过的日常',
                  alignLabelWithHint: true,
                ),
              ),
              if (mediaId.isNotEmpty) ...[
                const SizedBox(height: 18),
                const Text(
                  '今天的代表照片',
                  style: TextStyle(
                    color: _journalInk,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 10),
                ClipRRect(
                  borderRadius: BorderRadius.circular(24),
                  child: AspectRatio(
                    aspectRatio: 1.45,
                    child: Image.network(
                      api.mediaUrl(mediaId),
                      headers: api.mediaHeaders,
                      fit: BoxFit.cover,
                      errorBuilder: (_, __, ___) => const ColoredBox(
                        color: Color(0xfff5edf2),
                        child: Center(
                          child: Icon(
                            Icons.photo_outlined,
                            color: _journalMuted,
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              ],
              if (journal.savedQuote.isNotEmpty) ...[
                const SizedBox(height: 18),
                _QuoteBlock(
                  speaker: journal.quoteSpeaker == 'user' ? 'YoYo' : '小悠',
                  quote: journal.savedQuote,
                ),
              ],
              const SizedBox(height: 18),
              TextField(
                controller: tomorrow,
                minLines: 2,
                maxLines: 4,
                decoration: const InputDecoration(
                  labelText: '明天想一起做的事',
                  hintText: '没有明确约定时可以留空',
                  alignLabelWithHint: true,
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 18),
        FilledButton.icon(
          onPressed: saving || journal.confirmed ? null : onConfirm,
          icon: saving
              ? const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: Colors.white,
                  ),
                )
              : const Icon(Icons.auto_awesome_rounded),
          label: Text(journal.confirmed ? '已经收藏' : '确认并收藏这一天'),
          style: FilledButton.styleFrom(
            minimumSize: const Size.fromHeight(54),
            backgroundColor: _journalRose,
          ),
        ),
      ],
    );
  }
}

class _JournalPaper extends StatelessWidget {
  const _JournalPaper({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: xiaoyouCardSurface(context),
        borderRadius: BorderRadius.circular(32),
        border: Border.all(color: xiaoyouHairline(context), width: 1.2),
        boxShadow: const [
          BoxShadow(
            color: Color(0x174f2f40),
            blurRadius: 34,
            offset: Offset(0, 18),
          ),
        ],
      ),
      child: Padding(
        padding: const EdgeInsets.all(22),
        child: child,
      ),
    );
  }
}

class _JournalSeal extends StatelessWidget {
  const _JournalSeal();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 52,
      height: 52,
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xffffe7f1), Color(0xffeee5fa)],
        ),
        borderRadius: BorderRadius.circular(18),
        boxShadow: const [
          BoxShadow(color: Color(0x20b65b88), blurRadius: 18),
        ],
      ),
      child: const Icon(Icons.menu_book_rounded, color: _journalRose),
    );
  }
}

class _DraftBadge extends StatelessWidget {
  const _DraftBadge({required this.confirmed});

  final bool confirmed;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: confirmed ? const Color(0xffe7f7ef) : const Color(0xffffedf5),
        borderRadius: BorderRadius.circular(99),
      ),
      child: Text(
        confirmed ? '已收藏' : '草稿',
        style: TextStyle(
          color: confirmed ? const Color(0xff4e9773) : _journalRose,
          fontSize: 11,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

class _MoodPetal extends StatelessWidget {
  const _MoodPetal({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: xiaoyouSoftSurface(context),
        borderRadius: BorderRadius.circular(99),
        border: Border.all(color: xiaoyouHairline(context)),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.spa_rounded, size: 14, color: _journalRose),
            const SizedBox(width: 5),
            Text(
              label,
              style: const TextStyle(
                color: _journalInk,
                fontSize: 12,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _QuoteBlock extends StatelessWidget {
  const _QuoteBlock({required this.speaker, required this.quote});

  final String speaker;
  final String quote;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 16),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [Color(0xfffff5f9), Color(0xfff6f1fb)],
        ),
        borderRadius: BorderRadius.circular(22),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            speaker,
            style: const TextStyle(
              color: _journalRose,
              fontSize: 12,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 7),
          Text(
            '“$quote”',
            style: const TextStyle(
              color: _journalInk,
              height: 1.55,
              fontSize: 15,
            ),
          ),
        ],
      ),
    );
  }
}

class _JournalBackground extends StatelessWidget {
  const _JournalBackground();

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: xiaoyouIsDark(context)
              ? const [Color(0xff0f0f12), Color(0xff171318), Color(0xff13131a)]
              : const [Color(0xfffffbfd), Color(0xfffff3f8), Color(0xfff2eefb)],
        ),
      ),
    );
  }
}

class _JournalLoading extends StatelessWidget {
  const _JournalLoading();

  @override
  Widget build(BuildContext context) {
    return const Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          CircularProgressIndicator(color: _journalRose),
          SizedBox(height: 16),
          Text(
            '正在从今天真实发生的对话里整理草稿…',
            style: TextStyle(color: _journalMuted),
          ),
        ],
      ),
    );
  }
}

class _JournalError extends StatelessWidget {
  const _JournalError({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(
              Icons.auto_stories_outlined,
              size: 48,
              color: _journalMuted,
            ),
            const SizedBox(height: 16),
            Text(
              message,
              textAlign: TextAlign.center,
              style: const TextStyle(color: _journalMuted),
            ),
            const SizedBox(height: 14),
            OutlinedButton(onPressed: onRetry, child: const Text('重新整理')),
          ],
        ),
      ),
    );
  }
}

String _prettyDate(DateTime date) => '${date.year}年${date.month}月${date.day}日';
