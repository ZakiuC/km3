#!/usr/bin/env bash
# 新硬件驱动脚手架：从 device/drivers/_template/ 生成一个型号目录，
# 并把它的接线（CMake 挂载、CANopen 节点表、手册目录）一并接好。
#
# **默认 dry-run**，必须显式加 --apply 才写文件。这与本仓库既有的
# tools/provision_device.sh 同一纪律：一个会改动 device/CMakeLists.txt 与
# device/config/canopen_nodes.json 的脚本，不该在误敲一次回车后就生效。
#
# 用法见 usage()。
#
# 为什么要有这个脚本：新增一个型号的驱动，要同时改动**四处**——
# driver 目录、device/CMakeLists.txt 的挂载、CANopen 节点表、手册目录。
# 手抄容易漏掉 CMakeLists 那一处，而漏掉的后果是"目录在、没挂进 build、
# 静默不编译"，编译通过、测试通过、板上没有这个驱动。
# tools/check-drivers.sh 的第 (c) 条断言专门抓这个缺口，本脚本负责不制造它。
set -euo pipefail
cd "$(dirname "$0")/../.."   # 仓库根

APPLY=0
MODEL=""
BUS="can-2"                  # 默认：主板 CAN 第二个口（内核 can1，逻辑名 can-2）
NODE_ID=""
COUNT="1"
FORCE=0

usage() {
  cat <<EOF
usage: $0 <型号> [选项]

为 <型号> 生成一个硬件驱动骨架。**默认 dry-run**，只打印将要做的改动。

  --bus <逻辑名>     驱动挂在哪条总线上，默认 can-2。
                     逻辑名取值见 docs/架构设计/设备端程序架构.md §2.1.3 的映射表：
                       CAN-1 -> can0 -> can-1        主板第一个 CAN 口
                       CAN-2 -> can1 -> can-2        主板第二个 CAN 口
                       usb转can模块-1 -> can2 -> usb-can-1
                     注意内核 can2 是 USB 转 CAN 模块，**不是** CAN-2，下标差一位。
  --node-id <n>      CANopen 节点号，写进 device/config/canopen_nodes.json。
                     省略时按现有节点表自动取下一个可用编号。
  --count <n>        该型号几台（写入节点表），默认 1。
  --apply            真正写文件。不加则只打印。
  --dry-run          显式声明不写文件（本脚本默认行为，与 tools/provision_device.sh 同名同义）。
  --force            目标目录已存在时也继续（默认拒绝，避免覆盖已填好的手册引用）。
  -h, --help         打印本帮助并退出 0。

产出（--apply 时）：
  1. device/drivers/<型号>/                          从 _template 拷贝并替换占位
  2. device/CMakeLists.txt                           在「硬件驱动」标记区追加一行 add_subdirectory
  3. device/config/canopen_nodes.json                追加节点块
  4. docs/硬件手册/<型号>/README.md                  手册索引（从 _模板 拷贝）
  5. docs/硬件手册/<型号>/协议/README.md             协议原文投放目录
  6. 打印手册投放路径清单

下一步（脚本会再次提示）：
  python3 tools/hwdriver/gen-od.py --model <型号>
  bash tools/check-drivers.sh
EOF
}

die() { echo "错误：$*" >&2; exit 2; }

# ---- 参数解析 ----
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --bus)     [ $# -ge 2 ] || die "--bus 缺参数"; BUS="$2"; shift 2 ;;
    --node-id) [ $# -ge 2 ] || die "--node-id 缺参数"; NODE_ID="$2"; shift 2 ;;
    --count)   [ $# -ge 2 ] || die "--count 缺参数"; COUNT="$2"; shift 2 ;;
    --apply)   APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;   # 显式指定（默认即 dry-run，与 provision_device.sh 同名同义）
    --force)   FORCE=1; shift ;;
    -*)        echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
    *)         [ -z "$MODEL" ] || die "型号只能给一个（已给 $MODEL）"; MODEL="$1"; shift ;;
  esac
done

[ -n "$MODEL" ] || { usage >&2; exit 2; }

# ---- 型号校验 ----
# 型号会成为目录名、CMake 目标后缀、C++ 命名空间与宏名的一部分。
# 因此只允许这一点字符集，且必须字母开头——否则会生成
# add_subdirectory(2N2) 或 namespace km3::drivers::2n2 这类编不过的东西。
if ! printf '%s' "$MODEL" | LC_ALL=C grep -Eq '^[A-Za-z][A-Za-z0-9._-]*$'; then
  die "型号 '$MODEL' 不合法：只允许字母开头，后接字母/数字/._-（会成为目录名与 C++ 标识符）"
fi

# 驱动代号：型号里所有非字母数字折叠成下划线，再转小写。
# 例：PMP4010 -> pmp4010；P65MAX-S -> p65max_s。
CODE="$(printf '%s' "$MODEL" | LC_ALL=C tr -c 'A-Za-z0-9' '_' | tr 'A-Z' 'a-z')"
CODE="$(printf '%s' "$CODE" | sed 's/__*/_/g; s/^_//; s/_$//')"
[ -n "$CODE" ] || die "无法从 '$MODEL' 推出驱动代号"
# 毒化宏用大写代号：PMP4010_OD_VERIFIED
UCODE="$(printf '%s' "$CODE" | tr 'a-z' 'A-Z')"
# PascalCase 变体：模板的 _Model_ 出现在**类名**里（_Model_Driver / _Model_Manager），
# 替换成小写会产出不合规的类名，替换成全大写则与命名规范相悖。
PCODE="$(printf '%s' "${CODE:0:1}" | tr 'a-z' 'A-Z')${CODE:1}"
TARGET="km3_drv_${CODE}"

# ---- 路径 ----
DRIVER_DIR="device/drivers/${MODEL}"
TPL_DIR="device/drivers/_template"
DEV_CMAKE="device/CMakeLists.txt"
NODES_JSON="device/config/canopen_nodes.json"
MANUAL_DIR="docs/硬件手册/${MODEL}"
MANUAL_TPL="docs/硬件手册/_模板"

# CMake 追加區的标记文字。check-drivers.sh 用同一份字面量做反向完整性检查
# （每个 drivers/<型号>/ 都能从 device/CMakeLists.txt 到达），
# 两处必须一致，因此在这里定义一次，注释里写明另一处的名字。
MARK_BEGIN="# ---- 硬件驱动（由 tools/hwdriver/new-driver.sh 追加）----"
MARK_END="# ---- 硬件驱动结束 ----"

echo "==> 型号      : ${MODEL}"
echo "==> 驱动代号  : ${CODE}   （毒化宏 ${UCODE}_OD_VERIFIED，CMake 目标 ${TARGET}）"
echo "==> 总线      : ${BUS}"
echo "==> 模式      : $([ "$APPLY" -eq 1 ] && echo APPLY || echo 'DRY-RUN（加 --apply 才写文件）')"
echo

# ---- 前置检查：**先查全，再动手** ----
# 半途失败最糟：目录建了、CMakeLists 没改，于是产生"空目录挂不上"的中间态。
# 所以所有前置条件在写第一个字节之前一次性查完。
[ -d "$TPL_DIR" ] || die "缺少模板目录 ${TPL_DIR}/"
[ -f "$TPL_DIR/README.md" ] || die "缺少 ${TPL_DIR}/README.md"
[ -f "$DEV_CMAKE" ] || die "缺少 ${DEV_CMAKE}"
[ -f "$NODES_JSON" ] || die "缺少 ${NODES_JSON}"
[ -d "$MANUAL_TPL" ] || die "缺少手动模板目录 ${MANUAL_TPL}/"

if [ -e "$DRIVER_DIR" ] && [ "$FORCE" -eq 0 ]; then
  die "${DRIVER_DIR}/ 已存在。要覆盖请加 --force；已有目录里可能已经填好了手册引用注释。"
fi

# CMake 标记区必须已存在。**不自动创建**：自动往一个不认识的 CMakeLists 末尾
# 追加 add_subdirectory，只会让它在错误的位置生成（例如在 add_executable 之后），
# 而且失败时没有任何提示。找不到标记就明确报错，让人先手工放好标记。
if ! grep -qF -- "$MARK_BEGIN" "$DEV_CMAKE"; then
  die "${DEV_CMAKE} 里找不到标记「${MARK_BEGIN}」。
     本脚本不擅自创建标记：追加位置错了会静默生效。
     请先在驱动相关目标区手工加入这两行，再重跑：
       ${MARK_BEGIN}
       ${MARK_END}"
fi
if ! grep -qF -- "$MARK_END" "$DEV_CMAKE"; then
  die "${DEV_CMAKE} 里找不到结束标记「${MARK_END}」"
fi

# ---- 节点号：省略时自动取下一个可用编号 ----
# 已有节点占用的编号要避开。板级事实：can1 上将来 8 个节点
# （PMP4010×4 + 毛刷×2 + 刮条×2），编号冲突表现为"发下去两台一起动"。
if [ -z "$NODE_ID" ]; then
  NODE_ID="$(python3 - "$NODES_JSON" <<'PYEOF'
import io, json, sys
try:
    doc = json.load(io.open(sys.argv[1], encoding="utf-8"))
except FileNotFoundError:
    print(1); raise SystemExit(0)
used = set()
for n in doc.get("nodes", []):
    v = n.get("node_id")
    if isinstance(v, int):
        used.add(v)
i = 1
while i in used:
    i += 1
print(i)
PYEOF
)"
  echo "==> 节点号未指定，自动取下一个可用编号：${NODE_ID}"
fi

case "$NODE_ID" in
  ''|*[!0-9]*) die "--node-id 必须是正整数，得到 '${NODE_ID}'" ;;
esac
[ "$NODE_ID" -ge 1 ] && [ "$NODE_ID" -le 127 ] || die "CANopen 节点号必须在 1..127，得到 ${NODE_ID}"
case "$COUNT" in
  ''|*[!0-9]*) die "--count 必须是非负整数，得到 '${COUNT}'" ;;
esac

TOTAL_NODES="$((NODE_ID + COUNT - 1))"
[ "$TOTAL_NODES" -le 127 ] || die "节点号 ${NODE_ID} 起 ${COUNT} 台会超过 127"

# 已占用检查：同一编号被两个型号占用是接线层面的故障源。
python3 - "$NODES_JSON" "$NODE_ID" "$COUNT" <<'PYEOF'
import io, json, sys
path, first, count = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
try:
    doc = json.load(io.open(path, encoding="utf-8"))
except FileNotFoundError:
    doc = {}
mine = set(range(first, first + count))
for n in doc.get("nodes", []):
    v = n.get("node_id")
    if isinstance(v, int) and v in mine:
        print(f"错误：节点号 {v} 已被 {n.get('model', '?')} 占用", file=sys.stderr)
        raise SystemExit(2)
PYEOF

echo "==> 节点号    : ${NODE_ID}..$((NODE_ID + COUNT - 1))（${COUNT} 台）"
echo

# ---- 逐项执行 ----
# 每一项先打印，再在 APPLY 下执行。dry-run 与 apply 走**同一段代码**，
# 只用 APPLY 决定"打印还是执行"，避免两条路径各自漂移。
step() { echo "--> $*"; }
do_run() {
  if [ "$APPLY" -eq 1 ]; then
    "$@"
  else
    printf '    [dry-run] %s\n' "$*"
  fi
}

# ---- 1. 驱动目录：拷贝 + 替换占位 ----
step "1. 生成 ${DRIVER_DIR}/ （从 ${TPL_DIR}/ 拷贝并替换占位）"
do_run cp -a "$TPL_DIR" "$DRIVER_DIR"
if [ "$APPLY" -eq 1 ]; then
  # 替换所有文本文件里的占位。<代号>/<大写代号>/<型号>/<总线> 四个占位
  # 由模板定义；用 find + sed -i 而不是逐文件列举，
  # 这样模板将来加文件不必同步改本脚本。
  #
  # **不能用文件名白名单**。早期版本只列了 .md/.hpp/.cpp/.txt/.json，
  # 而模板里有一个 .py 工具——它被整个跳过，占位原样留下。
  # 那次是下面的自检抓到的（"仍存在未替换占位"），自检是最后一道防线，
  # 但不该是唯一一道：白名单每加一种文件类型都要人来同步，
  # 而人总会忘。因此改成黑名单——只跳过**已知二进制**，其余一律替换。
  #
  # 跳过 __pycache__：它里面的 .pyc 是二进制，sed 会把它改坏，
  # 而改坏的 .pyc 会让板上的 `import` 报 "bad magic number"，
  # 反查成本极高。
  find "$DRIVER_DIR" -type f \
       -not -path '*/__pycache__/*' \
       -not -name '*.pyc' \
       -not -name '*.eds' -not -name '*.dcf' \
       -not -name '*.pdf' -not -name '*.png' -not -name '*.jpg' \
       -not -name '*.zip' -not -name '*.gz' -not -name '*.bin' \
       -print0 \
    | xargs -0 sed -i \
        -e "s/<大写代号>/${UCODE}/g" \
        -e "s/<代号>/${CODE}/g" \
        -e "s/<型号>/${MODEL}/g" \
        -e "s|<总线>|${BUS}|g" \
        -e "s/__model__/${MODEL}/g" \
        -e "s/__MODEL__/${UCODE}/g" \
        -e "s/__Model__/${PCODE}/g" \
        -e "s/_MODEL_/${UCODE}_/g" \
        -e "s/_Model_/${PCODE}/g" \
        -e "s/_model_/${MODEL}/g"
  echo "    已替换占位：<型号>/${MODEL}、<代号>/${CODE}、<大写代号>/${UCODE}、<总线>/${BUS}"

  # 文件**名**也得改名。sed 只改内容，不改文件名 —— 而占位也出现在文件名里
  # （_model__driver.cpp、test__model__.cpp、tools/_model_.py）。
  # 漏改名的后果不是报错，是**编不过**：CMakeLists.txt 里已经替换成
  # driver/${CODE}_driver.cpp，而磁盘上那个文件还叫 _model__driver.cpp。
  # 错误信息是 "Cannot find source file"，指向一个看起来完全合理的路径。
  #
  # 顺序与内容替换一致：从最长的模式开始（_MODEL_ -> _Model_ -> _model_），
  # 否则 _Model_ 会吃掉 _MODEL_ 的前缀，产出残缺的 PascalCase 文件名。
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    # 文件名里的占位统一是 <代号>（模板刻意只用这一种，避免歧义）。
    #
    # 为什么不要 _model__driver.cpp / test__model__.cpp 那种双下划线词干：
    # 它的前后下划线归属不同（前者属于右侧配套，后者属于左侧前缀），
    # 没有任何单条 sed 规则能同时正确消解两者——
    # 实测把 `__model__` 聚合替换会得到 testyz100.cpp（丢了词干分隔），
    # 按 `_model_` 替换则得到 yz100_driver.cpp 与 test_yz100_.cpp（各对一半）。
    # 文件名不像文件内容，改名只发生一次、错了立刻编不过，
    # 所以这里选**能一条规则说清**的形式。
    newname="$(printf '%s' "$(basename "$f")" \
      | sed -e "s/<大写代号>/${UCODE}/g" \
            -e "s/<代号>/${CODE}/g" \
            -e "s/<型号>/${MODEL}/g")"
    [ "$newname" = "$(basename "$f")" ] || mv "$f" "$(dirname "$f")/$newname"
  done < <(find "$DRIVER_DIR" -depth \( -name '*<代号>*' -o -name '*<型号>*' -o -name '*<大写代号>*' \))

  echo "    已按占位规则改名：_model_* / _Model_* / _MODEL_* -> ${MODEL}*"
  # 全局替换后自检：不该再留任何占位符。
  # 漏替换的表现是 add_library(km3_drv_<代号>)，CMake 报语法错误，
  # 但错误信息指向 CMake 而不指向本脚本，反查成本高。
  # grep -rI 跳过二进制文件，因此上面的黑名单即使漏了一项也不会误报。
  if leftovers="$(grep -rIno '<\(代号\|型号\|总线\|大写代号\)>' "$DRIVER_DIR" || true)" && [ -n "$leftovers" ]; then
    echo "    !! 仍存在未替换占位：" >&2
    printf '%s\n' "$leftovers" | sed 's/^/       /' >&2
    exit 1
  fi
else
  printf '    [dry-run] 替换占位：<型号> -> %s，<代号> -> %s，<大写代号> -> %s，<总线> -> %s\n' \
    "$MODEL" "$CODE" "$UCODE" "$BUS"
fi

# ---- 2. device/CMakeLists.txt：在标记区追加一行 ----
step "2. ${DEV_CMAKE}：在「硬件驱动」标记区追加 add_subdirectory"
# 路径写成 drivers/<型号>，**不是** device/drivers/<型号>。
# add_subdirectory 的相对路径以**当前 CMakeLists 所在目录**为基准，
# 而本文件是 device/CMakeLists.txt —— 所以基准是 device/。
# 写成仓库相对路径会报
# "add_subdirectory given source "device/drivers/<型号>" which is not an existing directory"。
# 这个错误只在**配置期**出现，而 CI 的 template job 恰恰会配置一次，
# 因此它不会长期潜伏；但第一次踩到的人会对着一个不存在的目录名发懵。
ADD_LINE="add_subdirectory(drivers/${MODEL})"
if grep -qF -- "$ADD_LINE" "$DEV_CMAKE"; then
  echo "    已存在同一行，跳过（幂等）"
elif [ "$APPLY" -eq 1 ]; then
  # 插到结束标记**之前**，用 awk 按字面量定位。
  # 不用 sed 的地址范围：型号里可能含 `/`（如 P65MAX-S 不会，但 . 与 - 会），
  # sed 的正则转义会变成第二个 bug 源。
  awk -v beg="$MARK_BEGIN" -v end="$MARK_END" -v line="$ADD_LINE" '
    $0 == end && !done { print line; done = 1 }
    { print }
  ' "$DEV_CMAKE" > "${DEV_CMAKE}.tmp"
  mv "${DEV_CMAKE}.tmp" "$DEV_CMAKE"
  echo "    已追加：${ADD_LINE}"
else
  printf '    [dry-run] 在「%s」之前插入：%s\n' "$MARK_END" "$ADD_LINE"
fi

# 追加路径的自检：drivers/<型号> 相对 device/ 必须真实存在。
# 防的正是上面注释里那个错误——写完不验，配置期才炸。
#
# **只在 --apply 下做**。dry-run 时目录本来就不存在（第 1 步只打印不执行），
# 在这里判会必然失败，而失败信息看起来像"路径写错了"，
# 于是人跑去改一个其实正确的路径。实测踩过。
if [ "$APPLY" -eq 1 ] && [ ! -d "device/drivers/${MODEL}" ]; then
  echo "    !! ${ADD_LINE} 指向的目录不存在：device/drivers/${MODEL}" >&2
  exit 1
fi

# ---- 3. device/config/canopen_nodes.json：追加节点块 ----
step "3. ${NODES_JSON}：追加节点块"
if [ "$APPLY" -eq 1 ]; then
  python3 - "$NODES_JSON" "$MODEL" "$CODE" "$BUS" "$NODE_ID" "$COUNT" <<'PYEOF'
import io, json, sys, collections

path, model, code, bus, first, count = sys.argv[1:7]
first, count = int(first), int(count)

with io.open(path, encoding="utf-8") as fh:
    doc = json.load(fh, object_pairs_hook=collections.OrderedDict)

doc.setdefault("nodes", [])

# 总线字段两边都写：逻辑名（代码里用的）与内核接口名（现场 ip link 看到的）。
# 只写其一就会重演"CAN-2 到底是不是 can2"那个下标差一位的坑。
BUS_KERNEL = {"can-1": "can0", "can-2": "can1", "usb-can-1": "can2"}.get(bus, "")
for i in range(count):
    doc["nodes"].append(collections.OrderedDict([
        ("node_id", first + i),
        ("model", model),
        ("driver", code),
        ("bus", bus),
        ("bus_kernel", BUS_KERNEL),
        ("enabled", False),
        ("note", "由 tools/hwdriver/new-driver.sh 追加；节点号分配方式与波特率待厂家手册确认"),
    ]))

with io.open(path, "w", encoding="utf-8") as fh:
    fh.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
print(f"    已追加 {count} 个节点（node_id {first}..{first + count - 1}，bus {bus}）")
PYEOF
else
  printf '    [dry-run] 追加 %s 个节点：node_id %s..%s，model %s，bus %s，enabled false\n' \
    "$COUNT" "$NODE_ID" "$((NODE_ID + COUNT - 1))" "$MODEL" "$BUS"
fi

# ---- 4/5. 手册目录 ----
# 手册目录名用**型号**（厂家事实），不用中文别名——同一硬件在不同文档里
# 可能叫不同名字，用别名做目录名会让引用失稳。见 docs/硬件手册/README.md。
step "4. ${MANUAL_DIR}/README.md （手册索引，从 ${MANUAL_TPL} 拷贝）"
do_run mkdir -p "$MANUAL_DIR"
if [ "$APPLY" -eq 1 ]; then
  if [ -f "$MANUAL_TPL/README.md" ]; then
    sed -e "s/<型号>/${MODEL}/g" -e "s|<我们的叫法>|（未定）|g" \
        "$MANUAL_TPL/README.md" > "$MANUAL_DIR/README.md"
    echo "    已写入（占位 <型号> 已替换）"
  else
    printf '# %s — （未定）\n\n待填写。\n' "$MODEL" > "$MANUAL_DIR/README.md"
    echo "    模板缺失，已写最小骨架"
  fi
else
  printf '    [dry-run] mkdir -p %s && 由 %s/README.md 生成索引\n' "$MANUAL_DIR" "$MANUAL_TPL"
fi

step "5. ${MANUAL_DIR}/协议/README.md （协议原文投放目录）"
do_run mkdir -p "$MANUAL_DIR/协议"
if [ "$APPLY" -eq 1 ]; then
  if [ -f "$MANUAL_TPL/协议/README.md" ]; then
    sed -e "s/<型号>/${MODEL}/g" "$MANUAL_TPL/协议/README.md" > "$MANUAL_DIR/协议/README.md"
  else
    printf '# %s 通讯协议\n\n把厂家协议原文放这里：对象字典、报文说明、EDS/DCF、错误码表。\n' \
      "$MODEL" > "$MANUAL_DIR/协议/README.md"
  fi
  echo "    已写入"
else
  printf '    [dry-run] mkdir -p %s/协议 && 写入协议投放说明\n' "$MANUAL_DIR"
fi

# ---- 6. 手册投放路径清单 ----
# 这一步是脚本存在的第二个理由：驱动里的每个占位值都要能追溯到手册某份文件，
# 而"该放哪个文件"这件事只在人的脑子里。这里把它打印成清单，让人照着丢文件。
echo
echo "==> 手册投放路径清单（把厂家资料按此投放，驱动占位值才能逐条替换）"
echo
cat <<EOF
  docs/硬件手册/${MODEL}/
  ├── README.md                 资料索引与「未确认项」清单       <- 已生成骨架
  ├── 操作手册.md               节点号分配、上电默认状态、终端电阻
  ├── 参数表.md                 标度、零位偏置
  └── 协议/
      ├── README.md             协议投放说明                     <- 已生成骨架
      ├── 对象字典.md           索引、子索引、数据类型、默认值、标度
      ├── 控制字与状态字.md     位定义与状态机（逐位）
      ├── PDO映射.md            默认映射、可改写性、同步周期
      ├── EMCY错误码.md         厂商错误码 -> 可读原因
      └── ${MODEL}.eds          若厂家提供，**原样放，勿改写**

  对应的驱动侧位置：
    device/drivers/${MODEL}/driver/od.json
      —— 每条目的 "source" 字段填成 "<上面的仓库相对路径>:<页码或章节>"，
         再把 "verified" 改成 true。tools/check-manual-citations.sh 会检查
         该路径**真实存在**，指向空气即 CI 失败。
    device/drivers/${MODEL}/README.md
      —— 「归属」表里的总线/协议/台数/终端电阻待填。

  引用注释规则（源码里的寄存器地址、对象字典索引、标度因子都要能追溯）：
    od.json 的 source 字段 = 机器可读的引用，check-manual-citations.sh 直接校验。
    driver/*.cpp 里若出现硬编的索引/标度字面量，同一行必须有
    // 手册: docs/硬件手册/${MODEL}/...  形式的注释。
EOF
echo
echo "==> 下一步"
echo "    python3 tools/hwdriver/gen-od.py --model ${MODEL}     # 生成 od_generated.hpp 与 canopen_consts.py"
echo "    bash tools/check-drivers.sh                          # 四条解耦断言"
echo "    bash tools/check-manual-citations.sh                 # 溯源闸门"
if [ "$APPLY" -eq 0 ]; then
  echo
  echo "以上为 dry-run，未写任何文件。确认无误后加 --apply 重跑。"
fi
