# android — 上位机控制台（云卓 G30）

Android 原生应用，Kotlin + Jetpack Compose。架构见 `docs/架构设计/前端程序架构.md`。

## 与仓库其他部分的关系

- **协议定义共用**：`app/src/main/proto` 是指向 `../../../../device/proto` 的**符号链接**，
  由 `protobuf-javalite` 生成 Kotlin 代码。**不要在本目录另写一份 proto**——
  它是设备端与上位机共用的唯一协议契约，复制一份立刻产生两份真相。
- **不使用 JNI / NDK**。协议解析在 Kotlin 侧完成即可。

> 为什么用符号链接而不是在 `build.gradle.kts` 里配源集：protobuf-gradle-plugin
> 把 `proto` 作为**运行时扩展**挂到 `AndroidSourceSet`，而 Gradle 的 Kotlin DSL
> 不为 `NamedDomainObjectContainer` 元素的扩展生成访问器。三种 DSL 写法均实测失败
> （`Unresolved reference: proto` / `srcDir`）。`src/main/proto` 本就是该插件的
> 默认源目录，链接进去即可，无需任何 DSL 代码。

## 构建

环境要求（均已在本机验证）：

| 项 | 值 |
| --- | --- |
| JDK | **17**（AGP 8.2.0 要求；本机默认 java 是 8，必须显式指定 `JAVA_HOME`） |
| Gradle | **8.2**，由 wrapper 自带，无需系统安装 |
| Android SDK | platform 34、build-tools 34.0.0、NDK 26.1.10909125 |
| AGP / Kotlin | 8.2.0 / 1.9.20 |

```sh
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
./gradlew :app:assembleDebug        # 产出 app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:testDebugUnitTest    # 单元测试
```

`local.properties` 保存 `sdk.dir`，已 gitignore，**新克隆的仓库需自行创建**：
`echo "sdk.dir=$HOME/Android/Sdk" > local.properties`。

### gradle wrapper 的三个文件

`gradlew`、`gradle/wrapper/gradle-wrapper.jar`、`gradle/wrapper/gradle-wrapper.properties`
均已提交，版本对齐 Gradle 8.2。

**`distributionUrl` 指向腾讯镜像**，不是官方源：官方 `services.gradle.org` 在本机
下载中途 SSL 断开（实测），阿里镜像没有 `gradle/` 目录（404）。
换网络环境后可改回官方源，但改了要同步更新这行注释。

## 已知问题

| 项 | 说明 |
| --- | --- |
| 主题用平台 Material，不用 Material3 XML 主题 | `@style/Theme.Material3.DayNight` 来自 `com.google.android.material`（View 体系），本工程刻意不引入该依赖。启动窗口主题自定义在 `res/values/themes.xml`，parent 为 `android:Theme.Material.NoActionBar` |
| kapt 警告 `dagger.fastInit` 未被识别 | Hilt 2.48 与当前 kapt 的已知提示，不影响构建 |
| APK 约 18.8 MB（debug） | 未开启 minify；release 构建会走 R8 |

## 状态

骨架：Manifest（权限与明文流量策略）、Hilt 入口、帧编解码与 CRC16、
通道与命令状态模型。**构建已通过**（`BUILD SUCCESSFUL`，产出 APK）。
导航与业务页面待接入。
