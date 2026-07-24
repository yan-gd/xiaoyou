import 'package:flutter/services.dart';

class AppMediaSaveService {
  const AppMediaSaveService();

  static const _channel = MethodChannel('com.yoyo.xiaoyou/media');

  Future<void> saveImage({
    required Uint8List bytes,
    required String fileName,
    required String mimeType,
  }) async {
    await _channel.invokeMethod<void>('saveImageToGallery', {
      'bytes': bytes,
      'fileName': fileName,
      'mimeType': mimeType,
    }).timeout(const Duration(seconds: 30));
  }
}
