import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:xiaoyou_app/media_save_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const channel = MethodChannel('com.yoyo.xiaoyou/media');

  tearDown(() async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  test('image save sends bytes and metadata to the native gallery', () async {
    MethodCall? received;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      received = call;
      return null;
    });

    await const AppMediaSaveService().saveImage(
      bytes: Uint8List.fromList([1, 2, 3]),
      fileName: 'xiaoyou_1.png',
      mimeType: 'image/png',
    );

    expect(received?.method, 'saveImageToGallery');
    expect(received?.arguments['bytes'], Uint8List.fromList([1, 2, 3]));
    expect(received?.arguments['fileName'], 'xiaoyou_1.png');
    expect(received?.arguments['mimeType'], 'image/png');
  });
}
