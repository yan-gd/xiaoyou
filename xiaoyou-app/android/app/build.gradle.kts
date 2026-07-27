import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

val vivoPushAppId =
    providers.gradleProperty("XIAOYOU_VIVO_PUSH_APP_ID")
        .orElse(providers.environmentVariable("XIAOYOU_VIVO_PUSH_APP_ID"))
        .orElse("0")
val vivoPushAppKey =
    providers.gradleProperty("XIAOYOU_VIVO_PUSH_APP_KEY")
        .orElse(providers.environmentVariable("XIAOYOU_VIVO_PUSH_APP_KEY"))
        .orElse("disabled")

val releaseSigningProperties = Properties()
val releaseSigningPropertiesFile = rootProject.file("key.properties")
if (releaseSigningPropertiesFile.isFile) {
    FileInputStream(releaseSigningPropertiesFile).use(releaseSigningProperties::load)
}

fun releaseSigningValue(propertyName: String, environmentName: String): String? =
    releaseSigningProperties.getProperty(propertyName)
        ?.trim()
        ?.takeIf(String::isNotEmpty)
        ?: providers.environmentVariable(environmentName).orNull
            ?.trim()
            ?.takeIf(String::isNotEmpty)

val releaseStoreFile = releaseSigningValue("storeFile", "XIAOYOU_RELEASE_STORE_FILE")
val releaseStorePassword = releaseSigningValue("storePassword", "XIAOYOU_RELEASE_STORE_PASSWORD")
val releaseKeyAlias = releaseSigningValue("keyAlias", "XIAOYOU_RELEASE_KEY_ALIAS")
val releaseKeyPassword = releaseSigningValue("keyPassword", "XIAOYOU_RELEASE_KEY_PASSWORD")
val releaseStorePath = releaseStoreFile?.let(rootProject::file)
val hasReleaseSigning =
    releaseStorePath?.isFile == true &&
        releaseStorePassword != null &&
        releaseKeyAlias != null &&
        releaseKeyPassword != null

val requestedReleaseBuild =
    gradle.startParameter.taskNames.any { taskName ->
        taskName.contains("release", ignoreCase = true)
    }

if (requestedReleaseBuild && !hasReleaseSigning) {
    throw GradleException(
        "Release signing is not configured. Run " +
            "xiaoyou-app/tooling/create_release_keystore.ps1 and securely back up " +
            "android/keystore/xiaoyou-release.jks plus android/key.properties.",
    )
}

android {
    namespace = "com.yoyo.xiaoyou"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.yoyo.xiaoyou"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
        manifestPlaceholders["VIVO_PUSH_APP_ID"] = vivoPushAppId.get()
        manifestPlaceholders["VIVO_PUSH_APP_KEY"] = vivoPushAppKey.get()
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                storeFile = releaseStorePath
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

    buildTypes {
        release {
            signingConfig =
                if (hasReleaseSigning) signingConfigs.getByName("release") else null
        }
    }
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")
    implementation("com.umeng.umsdk:vivo-push:4.1.3.0")
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
