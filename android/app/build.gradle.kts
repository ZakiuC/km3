plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("kotlin-kapt")
    id("com.google.dagger.hilt.android")
    id("com.google.protobuf")
}

android {
    namespace = "com.km3.console"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.km3.console"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"

        // 设备直连与服务器地址在运行时由配置界面写入 DataStore，
        // 这里不留任何硬编码的 IP 或 device_id（见 前端程序架构.md §2.4）。
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    buildFeatures {
        compose = true
    }
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.4"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    // 不使用 JNI / NDK：协议用 protobuf-javalite 在 Kotlin 侧生成即可。
    // 见 系统总体架构.md §4.3。

}

// 协议定义与设备端共用同一份 proto，避免两端各写一套。
protobuf {
    protoc {
        artifact = "com.google.protobuf:protoc:3.24.4"
    }
    generateProtoTasks {
        all().forEach { task ->
            task.builtins {
                create("java") {
                    option("lite")
                }
            }
        }
    }
}

// 协议定义与设备端共用一份，通过符号链接挂进默认源目录：
//   app/src/main/proto -> ../../../../device/proto
//
// 为什么用链接而不是在 build.gradle.kts 里配源集：
//   protobuf-gradle-plugin 把 `proto` 作为**运行时扩展**挂到 AndroidSourceSet 上，
//   而 Gradle 的 Kotlin DSL 不为 NamedDomainObjectContainer 元素的扩展生成访问器。
//   实测三种写法都失败：
//     1. android {} 之外的 sourceSets.getByName("main") { proto { ... } }
//        -> Unresolved reference: proto
//     2. 移入 android {} 内，同样报 proto
//     3. 显式 extensions.getByName("proto", SourceDirectorySet::class.java)
//        -> 拿到扩展后 srcDir 仍不可解析
//   而 protobuf 插件的默认源目录就是 src/main/proto，链接进去即可，
//   不需要任何 DSL 代码。
//
// 用链接而非复制：proto 是设备端与上位机共用的**唯一**协议定义，
// 复制一份就会立刻产生两份真相，且签名/字段的改动只会落在其中一侧。

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.02.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.8.2")
    implementation("androidx.navigation:navigation-compose:2.7.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0")

    implementation("com.google.dagger:hilt-android:2.48")
    kapt("com.google.dagger:hilt-compiler:2.48")
    implementation("androidx.hilt:hilt-navigation-compose:1.1.0")

    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.google.protobuf:protobuf-javalite:3.24.4")

    implementation("androidx.datastore:datastore-preferences:1.0.0")
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    kapt("androidx.room:room-compiler:2.6.1")

    implementation("androidx.media3:media3-exoplayer:1.2.1")
    implementation("androidx.media3:media3-ui:1.2.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("io.mockk:mockk:1.13.9")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3")
}
