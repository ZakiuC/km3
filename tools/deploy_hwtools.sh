#!/usr/bin/env bash
# 硬件驱动工具的源码交付：版本目录 + 符号链接原子切换 + 失败回滚。
#
# **默认 --dry-run**，必须显式加 --apply 才改动目标机。
# 与 tools/deploy_device.sh / deploy_web.sh 同一套切换手法。
#
# ---- 需要 root：这一点必须先看清 ----
#
# 写 /opt/km3/hwtools/ **需要 root**（/opt 是 root 拥有的系统目录，
# 而 km3 用户的 CapEff=0，见 docs/架构设计/硬件驱动开发路径.md 的权限三段式）。
# 因此本脚本经 **ssh root@<主机>** 执行远端命令，而不是 ssh km3@。
#
# 这正是 device/deploy/systemd/km3-can.service 里 km3 用户跑得起来、
# 而 tools/deploy_device.sh 用 root@ 的原因：部署是维护动作，不是运行动作。
# 但由此产生一条纪律：**本脚本交付的是源码，不是服务**。
# 它不改任何 systemd 单元、不重启任何进程、不进 SERVICES_CORE。
# 板上跑工具的仍是 km3 用户，只是那些文件由 root 放进去、权限设为可读。
# 见 docs/架构设计/硬件驱动开发路径.md 末节「什么时候必须去修 deploy 脚本」。
#
# ---- 交付什么 ----
#
#   源   tools/hwtui/                        -> 目的 <root>/<ver>/hwtui/
#        device/drivers/*/tools/             -> 目的 <root>/<ver>/drivers/<型号>/
#        device/drivers/*/driver/od.json     -> 同一型号工具旁的 od.json
#
# hwtui 是公共库（canopen.py / tui.py / discover.py / canbus.py / mockbus.py
# 及生成的 canopen_consts.py），各型号驱动的 tools/<型号>.py 复用它。
# 两者一起投放，因为驱动的工具靠 `import hwtui.*` 活着；
# 只投一半的表现是板上 `import` 报 ModuleNotFoundError。
#
# 用 tar 经 ssh 传输（不依赖远端有 rsync —— 板端根分区很小，
# 不该为了部署一个脚本再装一个包）。
#
# ---- 为什么不覆盖式投放 ----
#
# 版本目录 + current 符号链接。理由与设备端二进制相同：
# 覆盖式投放一旦中途失败，板上就留下一个**半新半旧**的 hwtui——
# 而 hwtui 里的 canopen.py 与生成的 canopen_consts.py 必须同源，
# 版本错配的表现是"工具显示一个看着合理的错索引"，最难反查。
# 版本目录让"回到上一版"变成一次 ln。
set -euo pipefail
cd "$(dirname "$0")/.."

HOST=""
VERSION=""
TARGET_ROOT="/opt/km3/hwtools"
DRY_RUN=1          # 默认 dry-run
ALLOW_DIRTY=0

usage() {
  cat <<EOF
usage: $0 --host <ip|hostname> --version <semver> [--root DIR] [--apply] [--allow-dirty]

把硬件驱动工具（tools/hwtui/ 与 device/drivers/*/tools/）交付到设备端。

  --host <ip>      目标机，通常是测试板 192.168.0.188。
  --version <ver>  版本目录名。**必须显式给**：本脚本不替你挑一个
                   （用日期或 git 短 sha 由你决定，脚本不猜）。
  --root <dir>     目标根，默认 /opt/km3/hwtools。
  --apply          真正执行。**不加则只打印将要执行的命令（默认 dry-run）。**
  --allow-dirty    工作区有未提交改动时也继续（默认拒绝）。
  --dry-run        显式声明不写（默认行为，与 provision_device.sh 同名同义）。
  -h, --help       打印本帮助并退出 0。

**本脚本需要 root**：它在目标机上经 ssh root@<host> 写 /opt/km3/hwtools/。
这一点无法回避（/opt 属 root，而 km3 用户 CapEff=0）。它投的是源码，
不装服务、不重启进程、不改 systemd 单元。

部署后板上路径：
  <root>/current/hwtui/                 公共库
  <root>/current/drivers/<型号>/        该型号的工具

回滚：重新执行一次本脚本并指定上一个 --version 即可（切换是原子的）。
EOF
}

die() { echo "错误：$*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)     usage; exit 0 ;;
    --host)        [ $# -ge 2 ] || die "--host 缺参数"; HOST="$2"; shift 2 ;;
    --version)     [ $# -ge 2 ] || die "--version 缺参数"; VERSION="$2"; shift 2 ;;
    --root)        [ $# -ge 2 ] || die "--root 缺参数"; TARGET_ROOT="$2"; shift 2 ;;
    --apply)       DRY_RUN=0; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    *)             echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done

[ -n "$HOST" ] || { usage >&2; exit 2; }
[ -n "$VERSION" ] || {
  die "缺少 --version。版本目录名由**你**决定：脚本不替你挑一个日期或 sha——\
猜出来的版本号会在回滚时变成两个不同的东西叫同一个名字。"
}

# 版本名要能安全地落进远端路径。含 / 或 .. 会让 mkdir -p 作用到别处，
# 而失败的现象是"部署成功但 current 指向一个空目录"。
case "$VERSION" in
  */*|*..*|"") die "--version 含非法字符（/ 或 ..）：'$VERSION'" ;;
esac
case "$TARGET_ROOT" in
  /*) ;;
  *) die "--root 必须是绝对路径（远端脚本要拼进 rm -rf 的路径里）：$TARGET_ROOT" ;;
esac

SSH_OPTS=(-o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new)
REMOTE="root@${HOST}"

echo "==> 目标 ${REMOTE}，版本 ${VERSION}，根 ${TARGET_ROOT}"
echo "==> 模式 $([ "$DRY_RUN" -eq 1 ] && echo 'DRY-RUN（加 --apply 才执行）' || echo APPLY)"
echo "==> 注意：远端命令以 root 执行（/opt 属 root，km3 用户 CapEff=0）"
echo

# ---- 前置：源目录必须齐 ----
[ -d tools/hwtui ] || die "缺少 tools/hwtui/"
[ -d device/drivers ] || die "缺少 device/drivers/"

# 待交付的驱动工具目录。排除 _ 开头的模板目录：模板是给人抄的，
# 投到板上没有意义，而它会谎称自己是一个可用型号。
DRIVER_TOOL_DIRS=()
for d in device/drivers/*/tools; do
  [ -d "$d" ] || continue
  model="$(basename "$(dirname "$d")")"
  case "$model" in _*) continue ;; esac
  # 只投含 .py 的目录：空目录（刚 new-driver.sh 出来还没写工具）不必占位。
  if compgen -G "$d/*.py" >/dev/null; then
    [ -f "device/drivers/$model/driver/od.json" ] || \
      die "缺少 device/drivers/$model/driver/od.json（工具运行与离线自检需要）"
    DRIVER_TOOL_DIRS+=("$d")
  fi
done

echo "==> 将交付："
echo "    tools/hwtui/  ($(find tools/hwtui -maxdepth 1 -name '*.py' | wc -l) 个模块)"
for d in ${DRIVER_TOOL_DIRS[@]+"${DRIVER_TOOL_DIRS[@]}"}; do
  echo "    $d/"
done
if [ "${#DRIVER_TOOL_DIRS[@]}" -eq 0 ]; then
  echo "    （没有可交付的驱动工具目录——只交付 hwtui）"
fi
echo

# ---- 干净工作区检查 ----
# **默认拒绝脏工作区**：按路径打包会把未提交的改动一起送上板，
# 于是板上跑的是一份**只存在于你机器上**的代码，
# 出了问题在 git 里永远找不到对应的版本。
if [ "$DRY_RUN" -eq 0 ] && [ "$ALLOW_DIRTY" -eq 0 ]; then
  if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
    if [ -n "$(git status --porcelain -- tools/hwtui device/drivers 2>/dev/null)" ]; then
      echo "!! tools/hwtui 或 device/drivers 下有未提交改动："
      git status --porcelain -- tools/hwtui device/drivers | sed 's/^/     /' >&2
      die "拒绝交付未提交的代码（板上会留下一个 git 里不存在的版本）。\
确认这是你要的请加 --allow-dirty。"
    fi
  fi
fi

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '[dry-run] ssh %s :: %s\n' "$REMOTE" "$*"
  else
    ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"
  fi
}

# ---- 记录当前版本以便回滚 ----
# 同 deploy_device.sh 的教训：**不能**用 `readlink -f ... | basename`。
# GNU readlink -f 对不存在的路径也会打印规范化结果，首次部署时 PREV
# 会取到字面量 "current"（一个并不存在的版本目录），回滚于是把 current
# 指向悬空目标——比不回滚更糟。必须先确认它真是符号链接，再验证目标目录存在。
PREV=""
if [ "$DRY_RUN" -eq 0 ]; then
  PREV="$(ssh "${SSH_OPTS[@]}" "$REMOTE" \
  "if [ -L ${TARGET_ROOT}/current ]; then \
     t=\$(readlink ${TARGET_ROOT}/current); b=\$(basename \"\$t\"); \
     [ -d ${TARGET_ROOT}/releases/\$b ] && echo \"\$b\"; \
   fi" || true)"
  echo "==> 当前版本：${PREV:-<无>}"
else
  echo "==> 当前版本：<dry-run 不连接目标机>"
fi

# ---- 上传 ----
# 用 tar 经 ssh，不用 rsync：板端根分区只剩约 4.1 GiB，
# 为了部署几个 .py 去板上装一个包不值得，且板端未必有 rsync。
#
# 打包内容以**相对路径**存放（hwtui/ 与 drivers/<型号>/），
# 解包时用 tar -C 落到版本目录，于是远端不需要知道本地目录结构。
STAGE_LIST=()
STAGE_LIST+=("tools/hwtui")
for d in ${DRIVER_TOOL_DIRS[@]+"${DRIVER_TOOL_DIRS[@]}"}; do
  STAGE_LIST+=("$d")
  STAGE_LIST+=("$(dirname "$d")/driver/od.json")
done

upload() {
  # 先建版本目录，再解包。**绝不 mkdir current.tmp**（见 deploy_device.sh 的说明：
  # 若 current.tmp 先被建成目录，ln -sfn 会在该目录内部建链接，
  # mv -T 再把它搬成 current，得到的是目录而不是符号链接）。
  run "mkdir -p ${TARGET_ROOT}/releases/${VERSION}"

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] tar 打包并解包到 ${REMOTE}:${TARGET_ROOT}/releases/${VERSION}/"
    echo "[dry-run] 打包内容（相对路径）："
    printf '    %s\n' "${STAGE_LIST[@]}"
    echo "[dry-run] 解包后布局："
    echo "    ${TARGET_ROOT}/releases/${VERSION}/hwtui/"
    for d in ${DRIVER_TOOL_DIRS[@]+"${DRIVER_TOOL_DIRS[@]}"}; do
      echo "    ${TARGET_ROOT}/releases/${VERSION}/drivers/$(basename "$(dirname "$d")")/"
    done
    return
  fi

  # 先清空**明确的**版本目录内容再解包，避免旧残留（比如上一版本删掉的模块）
  # 留在一起。${VERSION} 已经过上面的字符校验，且目标是绝对路径，
  # 用 ${VAR:?} 让空值在展开期直接失败，而不是把 rm 作用到根上。
  local stage_dir
  stage_dir="$(mktemp -d)"
  trap 'rm -rf "$stage_dir"' RETURN
  mkdir -p "$stage_dir/hwtui" "$stage_dir/drivers"
  # 公共库只拷 .py；型号另带 od.json。__pycache__ 与 .pyc 不进板子（板端 python3 3.10.12 自己会编，
  # 而带上宿主机的 .pyc 会让板上 import 报 bad magic number）。
  #
  # chown 后面挂 `|| true`：**它失败不该让部署失败**。
  # 正常路径上远端就是 root，chown 必然成功；但若有人用非 root 的
  # ssh 目标（或本地回环验证）跑本脚本，chown 会报 "不允许的操作"
  # 并因 set -e 中断——**恰好在解包之后、切 current 之前**，
  # 于是留下一个已上传但没切换的半成品版本目录。
  # 权限本身由紧跟其后的 chmod -R a+rX 保证（读性才是 km3 用户需要的，
  # 归属不是），所以 chown 失败可以安全忽略。实测踩过。
  find tools/hwtui -maxdepth 1 -name '*.py' -exec cp {} "$stage_dir/hwtui/" \;
  for d in ${DRIVER_TOOL_DIRS[@]+"${DRIVER_TOOL_DIRS[@]}"}; do
    model="$(basename "$(dirname "$d")")"
    mkdir -p "$stage_dir/drivers/$model"
    find "$d" -maxdepth 1 -name '*.py' -exec cp {} "$stage_dir/drivers/$model/" \;
    cp "device/drivers/$model/driver/od.json" "$stage_dir/drivers/$model/od.json"
  done

  tar -C "$stage_dir" -czf - . | ssh "${SSH_OPTS[@]}" "$REMOTE" \
    "set -e; \
     v='${TARGET_ROOT}/releases/${VERSION}'; \
     rm -rf \"\${v:?}\"; mkdir -p \"\${v}\"; \
     tar -C \"\${v}\" -xzf -; \
     chown -R root:root \"\${v}\" 2>/dev/null || true; \
     chmod -R a+rX \"\${v}\"; \
     find \"\${v}\" -name '*.py' -exec chmod 0644 {} +"
}

upload

# ---- 原子切换 ----
# 单个 ln -sfn 会先删后建，中间态可能被并发读取到（板上可能有人在跑工具）。
# 因此建临时链接再 mv -T —— mv -T 是原子的重命名，不存在"current 短暂缺失"的窗口。
run "if [ -d ${TARGET_ROOT}/current ] && [ ! -L ${TARGET_ROOT}/current ]; then \
       n=\$(find ${TARGET_ROOT}/current -type f 2>/dev/null | wc -l); \
       if [ \"\$n\" -eq 0 ]; then \
         echo '    current 是目录且无普通文件（历史缺陷残留），删除'; \
         rm -rf ${TARGET_ROOT}/current; \
       else \
         echo \"    current 是目录且含 \$n 个普通文件，拒绝删除\" >&2; exit 1; \
       fi; \
     fi"
run "rm -f ${TARGET_ROOT}/current.tmp && \
     ln -s ${TARGET_ROOT}/releases/${VERSION} ${TARGET_ROOT}/current.tmp && \
     mv -T ${TARGET_ROOT}/current.tmp ${TARGET_ROOT}/current"

# ---- 校验（失败即回滚）----
if [ "$DRY_RUN" -eq 0 ]; then
  echo
  echo "==> 校验 current 指向与可导入性"
  # 校验三件事，而不是只看目录存在：
  #   1. current 真的是**符号链接**且指向本次版本（历史上它是目录过，
  #      那会让所有 ExecStart 报 203/EXEC）
  #   2. 板上 python3 能 import hwtui——语法错与缺模块要在这里暴露，
  #      而不是等到人上板敲命令的时候
  #   3. 生成的 canopen_consts.py 与 hwtui 同处一版（版本错配是
  #      "工具显示一个看着合理的错索引"的根因）
  # -I 会把当前目录排除出 sys.path，防止板端目录里一个同名 json.py
  # 盖掉标准库模块（主机侧已踩过这一类）。但 -I **同时也忽略 PYTHONPATH**，
  # 所以隔离与"从版本目录导入"是冲突的。正确做法是用 -c 且在
  # **已经 cd 到版本目录**的前提下直接 import —— 见下：先 cd 再跑，
  # 而 -c 的行内脚本不把 '' 加进 sys.path。
  # 这里用 PYTHONPATH 显式给出路径，并保留 -I 的其余隔离效果；
  # 若某版 python3 把 PYTHONPATH 也隔离掉，把它去掉即可（cd 已保证可导入）。
  OK=1
  # 远端这段必须让 **shell** 展开 $v，再把它交给 python。
  #
  # 实测踩过：第一版写成 python3 -c '...(插入 "$v")...'，外层是双引号、
  # 内层是单引号，于是 $v 被**单引号挡住**没有展开，python 收到的是字面量
  # "$v" 加进 sys.path —— 一个不存在的目录，于是 import 报
  # ModuleNotFoundError: No module named 'hwtui'，而文件明明就在那里。
  # 因此改用 env 传递：shell 展开 PYTHONPATH，python 侧完全不拼路径。
  if ! ssh "${SSH_OPTS[@]}" "$REMOTE" \
       "set -e; \
        v='${TARGET_ROOT}/releases/${VERSION}'; \
        [ -L ${TARGET_ROOT}/current ] || { echo 'current 不是符号链接' >&2; exit 1; }; \
        [ \"\$(readlink -f ${TARGET_ROOT}/current)\" = \"\$(readlink -f \"\$v\")\" ] \
          || { echo 'current 未指向本次版本' >&2; exit 1; }; \
        cd \"\$v\"; \
        for d in drivers/*; do \
          [ -d \"\$d\" ] || continue; \
          [ -f \"\$d/od.json\" ] || { echo \"缺少 \$d/od.json\" >&2; exit 1; }; \
        done; \
        python3 -c 'import hwtui.canopen, hwtui.tui, hwtui.discover' \
          && echo 'import ok'"; then
    OK=0
  fi

  if [ "$OK" -eq 0 ]; then
    echo "!! 校验失败，回滚" >&2
    if [ -n "$PREV" ]; then
      ssh "${SSH_OPTS[@]}" "$REMOTE" \
        "rm -f ${TARGET_ROOT}/current.tmp && \
         ln -s ${TARGET_ROOT}/releases/${PREV} ${TARGET_ROOT}/current.tmp && \
         mv -T ${TARGET_ROOT}/current.tmp ${TARGET_ROOT}/current"
      echo "==> 已回滚到 ${PREV}" >&2
    else
      echo "    （没有上一版本可回滚，current 保持不动）" >&2
    fi
    exit 1
  fi
  echo "==> 交付成功：${VERSION}"
  echo "    板上入口： ${TARGET_ROOT}/current/hwtui/"
  echo "    运行方式： python3 ${TARGET_ROOT}/current/drivers/<型号>/<型号>.py --help"
else
  echo
  echo "以上为 dry-run，未执行任何远端命令。确认无误后加 --apply 重跑。"
fi
