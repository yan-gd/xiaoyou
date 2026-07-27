import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'relationship_models.dart';
import 'xiaoyou_api.dart';

const _capsuleInk = Color(0xff402d38);
const _capsuleMuted = Color(0xff9c8792);
const _capsuleRose = Color(0xffb55886);
const _capsuleGold = Color(0xffd8b27b);

class TimeCapsuleScreen extends StatefulWidget {
  const TimeCapsuleScreen({
    super.key,
    required this.api,
    required this.entries,
  });

  final XiaoyouApi api;
  final List<RelationshipEntry> entries;

  @override
  State<TimeCapsuleScreen> createState() => _TimeCapsuleScreenState();
}

class _TimeCapsuleScreenState extends State<TimeCapsuleScreen>
    with SingleTickerProviderStateMixin {
  late final List<RelationshipEntry> _entries = List.of(widget.entries);
  late final AnimationController _glow = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2600),
  )..repeat();

  @override
  void dispose() {
    _glow.dispose();
    super.dispose();
  }

  Future<void> _compose() async {
    final draft = await showModalBottomSheet<_CapsuleDraft>(
      context: context,
      useSafeArea: true,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => const _ComposeCapsuleSheet(),
    );
    if (draft == null || !mounted) {
      return;
    }
    try {
      final entry = await widget.api.createTimeCapsule(
        title: draft.title,
        text: draft.text,
        unlockAt: draft.unlockAt,
      );
      if (!mounted) {
        return;
      }
      setState(() => _entries.insert(0, entry));
      HapticFeedback.mediumImpact();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('信已经封存，将在 ${_prettyTime(draft.unlockAt)} 打开'),
        ),
      );
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('这封信暂时没有封存成功')),
        );
      }
    }
  }

  Future<void> _open(RelationshipEntry entry) async {
    final stillLocked =
        entry.unlockTime?.isAfter(DateTime.now()) ?? entry.locked;
    if (stillLocked) {
      HapticFeedback.selectionClick();
      await showDialog<void>(
        context: context,
        builder: (_) => _LockedCapsuleDialog(entry: entry),
      );
      return;
    }
    RelationshipEntry opened = entry;
    if (entry.status != 'opened') {
      try {
        opened = await widget.api.openTimeCapsule(entry.id);
        if (!mounted) {
          return;
        }
        setState(() {
          final index = _entries.indexWhere((item) => item.id == opened.id);
          if (index >= 0) {
            _entries[index] = opened;
          }
        });
      } catch (_) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('信封暂时打不开，请稍后再试')),
          );
        }
        return;
      }
    }
    if (mounted) {
      await showDialog<void>(
        context: context,
        builder: (_) => _OpenedCapsuleDialog(entry: opened),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final capsules =
        _entries.where((entry) => entry.kind == 'capsule').toList();
    return Scaffold(
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _compose,
        backgroundColor: _capsuleRose,
        foregroundColor: Colors.white,
        icon: const Icon(Icons.edit_rounded),
        label: const Text('写一封信'),
      ),
      body: Stack(
        children: [
          const Positioned.fill(child: _CapsuleBackground()),
          SafeArea(
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(12, 8, 18, 8),
                  child: Row(
                    children: [
                      IconButton.filledTonal(
                        onPressed: () => Navigator.of(context).pop(_entries),
                        icon: const Icon(
                          Icons.arrow_back_ios_new_rounded,
                          size: 19,
                        ),
                      ),
                      const SizedBox(width: 12),
                      const Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              '写给未来',
                              style: TextStyle(
                                color: _capsuleInk,
                                fontSize: 23,
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                            Text(
                              '到了约定的时间，信才会真正打开',
                              style: TextStyle(
                                color: _capsuleMuted,
                                fontSize: 12,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                Expanded(
                  child: capsules.isEmpty
                      ? _EmptyCapsules(onCompose: _compose)
                      : ListView.separated(
                          padding: const EdgeInsets.fromLTRB(18, 18, 18, 110),
                          itemCount: capsules.length,
                          separatorBuilder: (_, __) =>
                              const SizedBox(height: 14),
                          itemBuilder: (_, index) => _CapsuleTile(
                            entry: capsules[index],
                            glow: _glow,
                            onTap: () => _open(capsules[index]),
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

class _CapsuleTile extends StatelessWidget {
  const _CapsuleTile({
    required this.entry,
    required this.glow,
    required this.onTap,
  });

  final RelationshipEntry entry;
  final Animation<double> glow;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final unlock = entry.unlockTime;
    return AnimatedBuilder(
      animation: glow,
      builder: (context, child) {
        final pulse = 0.55 + (glow.value - 0.5).abs() * 0.5;
        return DecoratedBox(
          decoration: BoxDecoration(
            color: const Color(0xfaffffff),
            borderRadius: BorderRadius.circular(28),
            border: Border.all(
              color: entry.locked
                  ? Color.lerp(
                      const Color(0x66d7b67f),
                      const Color(0xccbf6e98),
                      pulse,
                    )!
                  : const Color(0x99ffffff),
            ),
            boxShadow: const [
              BoxShadow(
                color: Color(0x16472a3b),
                blurRadius: 28,
                offset: Offset(0, 14),
              ),
            ],
          ),
          child: child,
        );
      },
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(28),
          child: Padding(
            padding: const EdgeInsets.all(18),
            child: Row(
              children: [
                Container(
                  width: 62,
                  height: 62,
                  decoration: BoxDecoration(
                    gradient: const LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [Color(0xffffe9f2), Color(0xffeee7fa)],
                    ),
                    borderRadius: BorderRadius.circular(22),
                  ),
                  child: Icon(
                    entry.locked
                        ? Icons.mark_email_unread_rounded
                        : Icons.drafts_rounded,
                    color: entry.locked ? _capsuleRose : _capsuleGold,
                    size: 29,
                  ),
                ),
                const SizedBox(width: 15),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        entry.title,
                        style: const TextStyle(
                          color: _capsuleInk,
                          fontSize: 17,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        entry.locked
                            ? '封存至 ${unlock == null ? '未来' : _prettyTime(unlock)}'
                            : entry.status == 'opened'
                                ? '已经拆开'
                                : '现在可以打开',
                        style: const TextStyle(
                          color: _capsuleMuted,
                          fontSize: 12,
                        ),
                      ),
                    ],
                  ),
                ),
                Icon(
                  entry.locked
                      ? Icons.lock_clock_rounded
                      : Icons.arrow_forward_ios_rounded,
                  color: _capsuleRose,
                  size: 19,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _ComposeCapsuleSheet extends StatefulWidget {
  const _ComposeCapsuleSheet();

  @override
  State<_ComposeCapsuleSheet> createState() => _ComposeCapsuleSheetState();
}

class _ComposeCapsuleSheetState extends State<_ComposeCapsuleSheet> {
  final _title = TextEditingController(text: '写给未来的我们');
  final _text = TextEditingController();
  DateTime _unlockAt = DateTime.now().add(const Duration(days: 1));

  @override
  void dispose() {
    _title.dispose();
    _text.dispose();
    super.dispose();
  }

  Future<void> _pickDate() async {
    final date = await showDatePicker(
      context: context,
      initialDate: _unlockAt,
      firstDate: DateTime.now(),
      lastDate: DateTime.now().add(const Duration(days: 3650)),
    );
    if (date == null || !mounted) {
      return;
    }
    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(_unlockAt),
    );
    if (time == null || !mounted) {
      return;
    }
    setState(() {
      _unlockAt = DateTime(
        date.year,
        date.month,
        date.day,
        time.hour,
        time.minute,
      );
    });
  }

  void _submit() {
    if (_text.text.trim().isEmpty || !_unlockAt.isAfter(DateTime.now())) {
      return;
    }
    Navigator.of(context).pop(
      _CapsuleDraft(
        title: _title.text.trim().isEmpty ? '写给未来' : _title.text.trim(),
        text: _text.text.trim(),
        unlockAt: _unlockAt,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: Material(
        color: const Color(0xfffffbfd),
        borderRadius: const BorderRadius.vertical(top: Radius.circular(34)),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 14, 20, 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: Container(
                  width: 44,
                  height: 4,
                  decoration: BoxDecoration(
                    color: const Color(0xffd8c2cf),
                    borderRadius: BorderRadius.circular(99),
                  ),
                ),
              ),
              const SizedBox(height: 20),
              const Text(
                '把此刻封进信里',
                style: TextStyle(
                  color: _capsuleInk,
                  fontSize: 22,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _title,
                decoration: const InputDecoration(labelText: '信封上的名字'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _text,
                autofocus: true,
                minLines: 4,
                maxLines: 8,
                decoration: const InputDecoration(
                  labelText: '想对未来说的话',
                  alignLabelWithHint: true,
                ),
              ),
              const SizedBox(height: 12),
              ListTile(
                onTap: _pickDate,
                contentPadding: const EdgeInsets.symmetric(horizontal: 4),
                leading: const Icon(
                  Icons.schedule_rounded,
                  color: _capsuleRose,
                ),
                title: const Text('打开时间'),
                subtitle: Text(_prettyTime(_unlockAt)),
                trailing: const Icon(Icons.chevron_right_rounded),
              ),
              const SizedBox(height: 10),
              FilledButton.icon(
                onPressed: _submit,
                icon: const Icon(Icons.lock_rounded),
                label: const Text('封存这封信'),
                style: FilledButton.styleFrom(
                  minimumSize: const Size.fromHeight(52),
                  backgroundColor: _capsuleRose,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _LockedCapsuleDialog extends StatelessWidget {
  const _LockedCapsuleDialog({required this.entry});

  final RelationshipEntry entry;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      icon: const Icon(
        Icons.lock_clock_rounded,
        color: _capsuleRose,
        size: 36,
      ),
      title: Text(entry.title),
      content: Text(
        '还没到拆信的时候。\n${_prettyTime(entry.unlockTime!)}，它会为你们亮起来。',
        textAlign: TextAlign.center,
      ),
      actionsAlignment: MainAxisAlignment.center,
      actions: [
        FilledButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('继续等它'),
        ),
      ],
    );
  }
}

class _OpenedCapsuleDialog extends StatefulWidget {
  const _OpenedCapsuleDialog({required this.entry});

  final RelationshipEntry entry;

  @override
  State<_OpenedCapsuleDialog> createState() => _OpenedCapsuleDialogState();
}

class _OpenedCapsuleDialogState extends State<_OpenedCapsuleDialog>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..forward();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ScaleTransition(
      scale: CurvedAnimation(
        parent: _controller,
        curve: Curves.easeOutBack,
      ),
      child: AlertDialog(
        icon: const Icon(
          Icons.mark_email_read_rounded,
          color: _capsuleGold,
          size: 42,
        ),
        title: Text(widget.entry.title),
        content: SingleChildScrollView(
          child: Text(
            '${widget.entry.body['text'] ?? ''}',
            style: const TextStyle(height: 1.65, fontSize: 15),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('收好这封信'),
          ),
        ],
      ),
    );
  }
}

class _EmptyCapsules extends StatelessWidget {
  const _EmptyCapsules({required this.onCompose});

  final VoidCallback onCompose;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(36),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(
              Icons.mark_email_unread_outlined,
              size: 64,
              color: _capsuleRose,
            ),
            const SizedBox(height: 18),
            const Text(
              '还没有写给未来的信',
              style: TextStyle(
                color: _capsuleInk,
                fontSize: 20,
                fontWeight: FontWeight.w800,
              ),
            ),
            const SizedBox(height: 8),
            const Text(
              '可以写给明天、生日、纪念日，或者很久以后的你们。',
              textAlign: TextAlign.center,
              style: TextStyle(color: _capsuleMuted, height: 1.55),
            ),
            const SizedBox(height: 18),
            OutlinedButton.icon(
              onPressed: onCompose,
              icon: const Icon(Icons.edit_rounded),
              label: const Text('写第一封'),
            ),
          ],
        ),
      ),
    );
  }
}

class _CapsuleBackground extends StatelessWidget {
  const _CapsuleBackground();

  @override
  Widget build(BuildContext context) {
    return const DecoratedBox(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            Color(0xfffffbfd),
            Color(0xfffff1f7),
            Color(0xfff0ecfa),
          ],
        ),
      ),
    );
  }
}

class _CapsuleDraft {
  const _CapsuleDraft({
    required this.title,
    required this.text,
    required this.unlockAt,
  });

  final String title;
  final String text;
  final DateTime unlockAt;
}

String _prettyTime(DateTime value) =>
    '${value.year}年${value.month}月${value.day}日 '
    '${value.hour.toString().padLeft(2, '0')}:'
    '${value.minute.toString().padLeft(2, '0')}';
