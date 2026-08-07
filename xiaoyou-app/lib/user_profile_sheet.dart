import 'package:flutter/material.dart';

import 'xiaoyou_api.dart';

class UserProfileSheet extends StatefulWidget {
  const UserProfileSheet({required this.api, required this.profile, super.key});

  final XiaoyouApi api;
  final XiaoyouUserProfile profile;

  @override
  State<UserProfileSheet> createState() => _UserProfileSheetState();
}

class _UserProfileSheetState extends State<UserProfileSheet> {
  late final TextEditingController _name;
  late final TextEditingController _about;
  DateTime? _birthday;
  bool _saving = false;
  String _error = '';

  @override
  void initState() {
    super.initState();
    _name = TextEditingController(text: widget.profile.displayName);
    _about = TextEditingController(text: widget.profile.aboutMe);
    _birthday = DateTime.tryParse(widget.profile.birthday);
  }

  @override
  void dispose() {
    _name.dispose();
    _about.dispose();
    super.dispose();
  }

  Future<void> _pickBirthday() async {
    final now = DateTime.now();
    final selected = await showDatePicker(
      context: context,
      initialDate: _birthday ?? DateTime(now.year - 20, now.month, now.day),
      firstDate: DateTime(1900),
      lastDate: now,
      helpText: '选择生日',
      cancelText: '暂不填写',
      confirmText: '确定',
    );
    if (selected != null && mounted) setState(() => _birthday = selected);
  }

  Future<void> _save() async {
    final name = _name.text.trim();
    if (name.isEmpty || name.length > 32) {
      setState(() => _error = '请填写 1–32 个字的称呼');
      return;
    }
    setState(() {
      _saving = true;
      _error = '';
    });
    try {
      final birthday = _birthday == null
          ? ''
          : '${_birthday!.year.toString().padLeft(4, '0')}-${_birthday!.month.toString().padLeft(2, '0')}-${_birthday!.day.toString().padLeft(2, '0')}';
      final value = await widget.api.updateAccountProfile(
        displayName: name,
        birthday: birthday,
        aboutMe: _about.text.trim(),
      );
      if (mounted) Navigator.pop(context, value);
    } catch (_) {
      if (mounted) setState(() => _error = '暂时保存不了，请稍后重试');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xfffff9fc),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: EdgeInsets.fromLTRB(
            24,
            18,
            24,
            24 + MediaQuery.viewInsetsOf(context).bottom,
          ),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 520),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      IconButton(
                        onPressed: () => Navigator.maybePop(context),
                        icon: const Icon(Icons.close_rounded),
                      ),
                      const Spacer(),
                      const Text('你的专属资料',
                          style: TextStyle(fontWeight: FontWeight.w700)),
                      const Spacer(),
                      const SizedBox(width: 48),
                    ],
                  ),
                  const SizedBox(height: 18),
                  const CircleAvatar(
                    radius: 42,
                    backgroundImage: AssetImage('assets/xiaoyou-avatar.png'),
                  ),
                  const SizedBox(height: 18),
                  const Text(
                    '让小悠更认识你',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                        fontSize: 26,
                        fontWeight: FontWeight.w800,
                        color: Color(0xff392a32)),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '你们已相识 ${widget.profile.relationshipDays} 天。这些资料只会进入你的独立资料文档。',
                    textAlign: TextAlign.center,
                    style:
                        const TextStyle(color: Color(0xff89757f), height: 1.5),
                  ),
                  const SizedBox(height: 28),
                  _field(_name, '希望小悠怎么称呼你', Icons.favorite_border_rounded),
                  const SizedBox(height: 14),
                  InkWell(
                    onTap: _pickBirthday,
                    borderRadius: BorderRadius.circular(20),
                    child: InputDecorator(
                      decoration: const InputDecoration(
                        labelText: '生日（选填）',
                        prefixIcon: Icon(Icons.cake_outlined),
                      ),
                      child: Text(
                        _birthday == null
                            ? '告诉小悠后，她会记得这一天'
                            : '${_birthday!.year} 年 ${_birthday!.month} 月 ${_birthday!.day} 日',
                        style: TextStyle(
                            color: _birthday == null
                                ? const Color(0xffa29099)
                                : const Color(0xff3b2d34)),
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  _field(
                    _about,
                    '想让小悠了解的你（选填）',
                    Icons.auto_awesome_outlined,
                    maxLines: 4,
                    maxLength: 300,
                  ),
                  if (_error.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    Text(_error,
                        style: const TextStyle(color: Color(0xffb13f68))),
                  ],
                  const SizedBox(height: 20),
                  SizedBox(
                    height: 54,
                    child: FilledButton(
                      onPressed: _saving ? null : _save,
                      child: _saving
                          ? const SizedBox(
                              width: 22,
                              height: 22,
                              child: CircularProgressIndicator(
                                  strokeWidth: 2, color: Colors.white))
                          : const Text('保存，让小悠记住'),
                    ),
                  ),
                  const SizedBox(height: 10),
                  TextButton(
                    onPressed:
                        _saving ? null : () => Navigator.maybePop(context),
                    child: const Text('稍后再填写'),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _field(
    TextEditingController controller,
    String label,
    IconData icon, {
    int maxLines = 1,
    int? maxLength,
  }) {
    return TextField(
      controller: controller,
      maxLines: maxLines,
      maxLength: maxLength,
      decoration: InputDecoration(labelText: label, prefixIcon: Icon(icon)),
    );
  }
}
