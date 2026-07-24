import Flutter
import Photos
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    guard let registrar = engineBridge.pluginRegistry.registrar(
      forPlugin: "XiaoyouMedia"
    ) else {
      return
    }
    let mediaChannel = FlutterMethodChannel(
      name: "com.yoyo.xiaoyou/media",
      binaryMessenger: registrar.messenger()
    )
    mediaChannel.setMethodCallHandler { [weak self] call, result in
      guard call.method == "saveImageToGallery" else {
        result(FlutterMethodNotImplemented)
        return
      }
      self?.saveImageToGallery(call: call, result: result)
    }
  }

  private func saveImageToGallery(call: FlutterMethodCall, result: @escaping FlutterResult) {
    guard
      let arguments = call.arguments as? [String: Any],
      let typedData = arguments["bytes"] as? FlutterStandardTypedData,
      let image = UIImage(data: typedData.data)
    else {
      result(
        FlutterError(
          code: "invalid_image",
          message: "Image data is empty or invalid.",
          details: nil
        )
      )
      return
    }

    let performSave = {
      PHPhotoLibrary.shared().performChanges {
        PHAssetChangeRequest.creationRequestForAsset(from: image)
      } completionHandler: { saved, error in
        DispatchQueue.main.async {
          if saved {
            result(nil)
          } else {
            result(
              FlutterError(
                code: "photo_save_failed",
                message: error?.localizedDescription ?? "Unable to save image.",
                details: nil
              )
            )
          }
        }
      }
    }

    if #available(iOS 14, *) {
      PHPhotoLibrary.requestAuthorization(for: .addOnly) { status in
        if status == .authorized || status == .limited {
          performSave()
        } else {
          DispatchQueue.main.async {
            result(
              FlutterError(
                code: "photo_permission_denied",
                message: "Photo library permission was denied.",
                details: nil
              )
            )
          }
        }
      }
    } else {
      PHPhotoLibrary.requestAuthorization { status in
        if status == .authorized {
          performSave()
        } else {
          DispatchQueue.main.async {
            result(
              FlutterError(
                code: "photo_permission_denied",
                message: "Photo library permission was denied.",
                details: nil
              )
            )
          }
        }
      }
    }
  }
}
