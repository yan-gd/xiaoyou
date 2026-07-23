allprojects {
    repositories {
        maven("https://maven.aliyun.com/repository/google")
        maven("https://maven.aliyun.com/repository/central")
        google()
        mavenCentral()
    }
}

val newBuildDir: Directory =
    rootProject.layout.buildDirectory
        .dir("../../build")
        .get()
rootProject.layout.buildDirectory.value(newBuildDir)

subprojects {
    val newSubprojectBuildDir: Directory = newBuildDir.dir(project.name)
    project.layout.buildDirectory.value(newSubprojectBuildDir)
}
subprojects {
    project.evaluationDependsOn(":app")
}

// Flutter Secure Storage's JNI bridge currently pins compileSdk 35. The app
// already targets the installed Android 36 SDK, so keep every library module
// on that same compatible SDK instead of requiring a redundant platform copy.
subprojects {
    if (name != "app") {
        afterEvaluate {
            extensions
                .findByType(com.android.build.api.dsl.LibraryExtension::class.java)
                ?.compileSdk = 36
        }
    }
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}
