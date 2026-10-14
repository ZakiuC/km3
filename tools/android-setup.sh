#!/usr/bin/env bash
# km3 Android 主机环境自检（以普通用户运行，**不要 sudo**）
#
# 检查项来自 android/README.md 的环境要求表。本脚本只**报告**，不改系统：
# 需要 sudo 的命令打印出来由人执行。
set -euo pipefail

SDK="${ANDROID_HOME:-$HOME/Android/Sdk}"
JDK17=/usr/lib/jvm/java-17-openjdk-amd64
ok=0; bad=0
report() {
  if [ "$1" = 0 ]; then echo "  [OK]  $2"; ok=$((ok+1));
  else echo "  [缺失] $2"; bad=$((bad+1)); fi
}

echo "== Android 构建环境自检 =="
echo

# --- JDK 17 ---
if [ -x "$JDK17/bin/java" ]; then
  report 0 "JDK 17: $("$JDK17/bin/java" -version 2>&1 | head -1)"
else
  report 1 "JDK 17 未安装（AGP 8.x 必需）"
  echo "         sudo apt-get install -y openjdk-17-jdk"
fi
if [ "${JAVA_HOME:-}" = "$JDK17" ]; then
  report 0 "JAVA_HOME 已指向 JDK 17"
else
  report 1 "JAVA_HOME 未指向 JDK 17（当前：${JAVA_HOME:-<未设置>}）"
  echo "         export JAVA_HOME=$JDK17"
  echo "         本机默认 java 是 8，不显式指定会构建失败。"
fi

# --- SDK 包 ---
report $([ -d "$SDK" ] && echo 0 || echo 1) "SDK 根目录：$SDK"
for pkg in "platforms/android-34" "build-tools/34.0.0" "ndk/26.1.10909125" "platform-tools"; do
  report $([ -d "$SDK/$pkg" ] && echo 0 || echo 1) "$pkg"
done
if [ ! -d "$SDK/platforms/android-34" ]; then
  echo "         安装：\"$SDK/cmdline-tools/latest/bin/sdkmanager\" \\"
  echo "           \"platforms;android-34\" \"build-tools;34.0.0\" \"ndk;26.1.10909125\""
  echo "         sdkmanager --licenses 需交互接受许可。"
fi

# --- 工程侧 ---
report $([ -f android/local.properties ] && echo 0 || echo 1) "android/local.properties（sdk.dir）"
report $([ -x android/gradlew ] && echo 0 || echo 1) "android/gradlew"
report $([ -f android/gradle/wrapper/gradle-wrapper.jar ] && echo 0 || echo 1) \
       "gradle/wrapper/gradle-wrapper.jar"
report $([ -L android/app/src/main/proto ] && echo 0 || echo 1) \
       "app/src/main/proto 符号链接（指向 device/proto）"

echo
echo "通过 $ok 项，缺失 $bad 项。"
if [ "$bad" -gt 0 ]; then
  echo "补齐后执行： export JAVA_HOME=$JDK17 && cd android && ./gradlew :app:assembleDebug"
  exit 1
fi
echo "环境就绪。构建： cd android && ./gradlew :app:assembleDebug"
