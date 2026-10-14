#!/usr/bin/env bash
# 硬件驱动的解耦检查器。CI 的 drivers job 调用本脚本。
#
# 五条断言（全部必须过，任一条不过即退出 1）：
#
#   (a) 链接白名单：每个 device/drivers/<型号>/CMakeLists.txt 里
#       target_link_libraries 的目标集合必须是
#       {km3_core, km3_hal, km3_canopen, km3_driver_api} 的子集。
#       出现 km3_drv_<别的型号> 即失败——那是横向链接。
#   (b) 目录内不得引用别的驱动：grep include 与型号字面量，
#       命中 drivers/<别的型号>/ 即失败。
#   (c) 反向完整性：device/drivers/ 下每个目录都必须能从
#       device/CMakeLists.txt 到达。
#   (d) 五件套：每个驱动目录恰含 README.md / CONTRACT.md / driver/ / tests/ / tools/。
#   (e) 物理标识：device/drivers/ 全树（含 _template）不得出现内核接口名，
#       物理标识只允许出现在 device/config/device.json。
#
# 为什么 (c) 最关键：
#
#   目录存在但没挂进 build 是**静默**的——CMake 配置通过、编译通过、
#   测试通过，板上就是没有这个驱动。没有任何其它信号能暴露它。
#   tools/check-docs.sh 的单向树规则（文档提到 -> 磁盘存在）方向相反，
#   补不上这个缺口，所以这里必须有正向的那一半。
#
# 退出码：0 全部通过；1 有违规；2 脚本/环境错误（缺文件等）。
set -uo pipefail
cd "$(dirname "$0")/.."

DRIVERS_DIR="device/drivers"
DEV_CMAKE="device/CMakeLists.txt"

# 链接白名单。与 device/drivers/_template/CMakeLists.txt 里那份
# KM3_DRV_DEPENDENCIES 必须一致；两处不一致时以架构层的定义为准：
# km3_core（实时原语）/ km3_hal（硬件抽象）/ km3_canopen（协议栈）/
# km3_driver_api（驱动统一接口）。
# 改这里必须同时改模板，否则新驱动会照着一个过期的白名单生成。
ALLOWED_DEPS="km3_core km3_hal km3_canopen km3_driver_api"

# 五件套。(d) 的清单，也是 (b) 里"另一个驱动目录"的判定基准。
FIVESET_FILES="README.md CONTRACT.md"
FIVESET_DIRS="driver tests tools"

# 模板目录不参与检查：它天然含占位型号，且不该被挂进主构建。
# 判据用**前导下划线**而不是硬编 "_template"：约定是"以下划线开头的是模板/私有目录"，
# 将来加 _scaffold 之类不必改这里。
is_template_dir() { case "$1" in _*) return 0 ;; *) return 1 ;; esac; }

if [ ! -d "$DRIVERS_DIR" ]; then
  # 允许"尚未创建"：本脚本先于第一个驱动落地进 CI。
  # 但要**显式说出来**，而不是静默通过——静默通过会让"检查器其实没跑"与
  # "检查通过"看起来一样。
  echo "  未找到 $DRIVERS_DIR/，跳过（尚未创建任何驱动）"
  echo "结果：无驱动可查（不是通过——是还没有对象）"
  exit 0
fi
[ -f "$DEV_CMAKE" ] || { echo "错误：缺少 $DEV_CMAKE" >&2; exit 2; }

FAIL=0
note() { printf '  %s\n' "$*"; }
violation() { printf '  [违规] %s\n' "$*"; FAIL=1; }

# ---- 收集驱动目录 ----
DRIVER_MODELS=()
for d in "$DRIVERS_DIR"/*/; do
  [ -d "$d" ] || continue
  m="$(basename "$d")"
  is_template_dir "$m" && continue
  DRIVER_MODELS+=("$m")
done

echo "==> 驱动解耦检查（$DRIVERS_DIR/，${#DRIVER_MODELS[@]} 个驱动）"

# ---- (d) 五件套：先查，因为后面的断言都假定目录结构成立 ----
for m in "${DRIVER_MODELS[@]:-}"; do
  [ -n "$m" ] || continue
  d="$DRIVERS_DIR/$m"
  for f in $FIVESET_FILES; do
    [ -f "$d/$f" ] || violation "$m 缺少 $f（五件套缺件）"
  done
  for sub in $FIVESET_DIRS; do
    [ -d "$d/$sub" ] || violation "$m 缺少 $sub/（五件套缺件）"
  done
done
note "(d) 五件套：检查 ${#DRIVER_MODELS[@]} 个驱动"

# ---- (a) 链接白名单 ----
# 只在驱动自己的 CMakeLists.txt 上查。用完整词边界匹配目标名，
# 避免 km3_core 命中 km3_corex 这类前缀（实测最容易写错的一处）。
for m in "${DRIVER_MODELS[@]:-}"; do
  [ -n "$m" ] || continue
  cml="$DRIVERS_DIR/$m/CMakeLists.txt"
  [ -f "$cml" ] || continue

  # 取所有 target_link_libraries(...) 调用块里的裸目标名。
  # 目标名可能跨行、可能带 PUBLIC/PRIVATE/INTERFACE 关键字、
  # 可能用 ${VAR} 展开（本仓库的模板就是这么写的），
  # 因此用 python 解析而不是 grep：grep 会把 ${VAR} 与注释里的目标名一并算进来。
  python3 - "$cml" "$ALLOWED_DEPS" "$m" <<'PYEOF'
import io, re, sys

cml, allowed_raw, model = sys.argv[1], sys.argv[2], sys.argv[3]
allowed = set(allowed_raw.split())

text = io.open(cml, encoding="utf-8").read()
# 先剥掉注释：注释里提到 km3_drv_other 是在**解释**禁止横向链接，
# 不是在横向链接。不剥注释会把文档写成违规。
text = re.sub(r"#[^\n]*", "", text)

fail = False


def call_bodies(src, name):
    """取出 name( ... ) 的正文，按**配对括号**切分。

    为什么不能写成 `name\s*\(([^)]*)\)`：CMake 的参数里会出现嵌套括号
    （generator expression $<TARGET_NAME:foo>、以及 ${VAR} 之外的分组），
    而 [^)]* 会在第一个 ) 处停下。更糟的是它对**跨调用**的文本也会匹配：
    实测中 `target_link_libraries(km3_drv_a PRIVATE km3_core)` 的目标名与
    下一个调用的目标名被拼进了同一段正文，于是检查器把驱动自己报成了
    「白名单外的目标」。手工数括号是唯一的正确做法。
    """
    out = []
    for m in re.finditer(re.escape(name) + r"\s*\(", src):
        i = m.end()          # 指向 '(' 之后
        depth = 1
        while i < len(src) and depth:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        out.append(src[m.end():i - 1 if depth == 0 else i])
    return out


for body in call_bodies(text, "target_link_libraries"):
    tokens = body.split()
    # **第一个 token 是被链接的目标本身**，不是依赖。实测踩过：
    # 不跳过它，每个驱动的构建文件都会报一条「含白名单外的 km3 目标 km3_drv_<自己>」
    # —— 而那是这条断言自己在报告自己，属于纯噪声。
    # 跳过它之后这条断言才真的在查依赖。
    tokens = tokens[1:]
    skip = {"PUBLIC", "PRIVATE", "INTERFACE", "LINK_PUBLIC", "LINK_PRIVATE"}
    for tok in tokens:
        if tok in skip:
            continue
        # 变量引用：${KM3_DRV_RESOLVED_DEPS} 之类。展开它不在本脚本职责内
        # （要展开就得实现 CMake），但**必须检查它有没有可能是兄弟驱动**：
        # 因此把变量名本身记下来，并单独断言"驱动不得把变量名指向 km3_drv_"。
        if tok.startswith("${") or tok.startswith("$<") or tok.startswith("$"):
            continue
        if tok.startswith("km3_drv_"):
            # 自己的驱动目标：测试目标 test_<型号> 要链它，这是**必须**的，
            # 不算越界。若不在这里 continue，它会掉进下面的白名单分支被判违规
            # ——实测踩过，每个驱动都会恒红一条「含白名单外的目标 km3_drv_<自己>」。
            if tok == "km3_drv_" + model:
                continue
            print("  [违规] %s: target_link_libraries 指向兄弟驱动 %s"
                  "（型号之间不得横向链接，协同请走 km3_driver_api 或上层编排）"
                  % (cml, tok))
            fail = True
            continue
        if tok not in allowed and (tok.startswith("km3_") or tok.startswith("km3-drv")):
            print("  [违规] %s: target_link_libraries 含白名单外的 km3 目标 %s"
                  "（白名单：%s）" % (cml, tok, " / ".join(sorted(allowed))))
            fail = True
sys.exit(1 if fail else 0)
PYEOF
  [ $? -eq 0 ] || FAIL=1
done
LINK_CHECKED="${#DRIVER_MODELS[@]}"
note "(a) 链接白名单：检查 $LINK_CHECKED 个驱动的 CMakeLists，白名单 = $ALLOWED_DEPS"

# ---- (b) 驱动之间不得互相引用 ----
# 两件事都查：
#   1. include 路径里出现 drivers/<别的型号>/ —— 直接跨目录包含
#   2. 目录内出现**别的**型号的字面量 —— 通常出现在条件编译或注释掉的分支里
# 自己型号的字面量是允许的（驱动当然会提到自己），因此判据是"是否**属于另一个驱动目录**"，
# 而不是"是否像个型号名"。后者需要一份型号清单，会与设备清单漂移。
if [ "${#DRIVER_MODELS[@]}" -gt 0 ]; then
  for m in "${DRIVER_MODELS[@]}"; do
    d="$DRIVERS_DIR/$m"
    for other in "${DRIVER_MODELS[@]}"; do
      [ "$other" = "$m" ] && continue
      # 1. include 另一个驱动目录
      hits="$(grep -rn --include='*.cpp' --include='*.hpp' --include='*.h' \
                   --include='*.py' --include='*.txt' --include='*.md' \
                   -e "drivers/$other/" "$d" 2>/dev/null || true)"
      if [ -n "$hits" ]; then
        while IFS= read -r line; do
          violation "$m 引用了 $other 的路径：$line"
        done <<< "$hits"
      fi
      # 2. 型号字面量（作为独立词出现）。用 -w 词边界，
      #    否则 pmp4010 会命中 pmp40100 这类前缀，产生假报。
      hits="$(grep -rnw --include='*.cpp' --include='*.hpp' --include='*.h' \
                   --include='*.py' -e "$other" "$d" 2>/dev/null || true)"
      if [ -n "$hits" ]; then
        while IFS= read -r line; do
          violation "$m 出现 $other 的型号字面量：$line"
        done <<< "$hits"
      fi
    done
  done
fi
note "(b) 驱动互引：检查 ${#DRIVER_MODELS[@]} 个驱动之间的两两引用"

# ---- (c) 反向完整性：每个驱动都能从 device/CMakeLists.txt 到达 ----
# 判据是"device/CMakeLists.txt 里有指向该驱动目录的 add_subdirectory"。
# 三种写法都算到达：
#   add_subdirectory(drivers/<型号>)                 相对 device/
#   add_subdirectory(device/drivers/<型号>)          仓库相对（CI 模板 job 曾用过）
#   add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/drivers/<型号>)
# 不在 marker 区内也认——marker 是人写的，漏写 marker 不该让一个
# 已经挂好的驱动被判成没挂；(c) 查的是**可达性**，不是"写在哪一行"。
if [ "${#DRIVER_MODELS[@]}" -gt 0 ]; then
  for m in "${DRIVER_MODELS[@]}"; do
    d="$DRIVERS_DIR/$m"
    # 该驱动是否被某个条件守卫挡住（if(TARGET ...) / return()）？
    # 挡住的驱动**仍然算到达**：它的 CMakeLists 被配置到了，
    # 只是自行选择跳过。判据是 add_subdirectory 存在，不是它一定产出目标。
    if grep -qE "add_subdirectory\s*\(\s*(\\\\$\{CMAKE_CURRENT_SOURCE_DIR\}/)?(device/)?drivers/$m([\\s)]|$)" "$DEV_CMAKE"; then
      continue
    fi
    violation "$m 无法从 $DEV_CMAKE 到达（没有 add_subdirectory）—— \
目录存在但没挂进 build 是静默缺口：配置通过、编译通过、板上没有这个驱动"
  done
fi
note "(c) 反向完整性：检查 ${#DRIVER_MODELS[@]} 个驱动是否都能从 $DEV_CMAKE 到达"

# ---- (e) 驱动目录不得出现物理标识 ----
#
# 由来：模板工具曾把 `--interface` 的默认值写成内核接口名。每个复制出去的驱动
# 都原样继承它，而物理标识的**唯一来源应当是 device/config/device.json**——
# 同一事实两处定义迟早分家，分家的表现是"工具连的是另一条总线"，
# 现场最难反查的一类故障。该缺陷是被人工审查发现的，不是被 CI 拦下的：
# (a)-(d) 四条都按 DRIVER_MODELS 走，而 _template 不在其中，正好漏网。
#
# 因此本断言扫描**整棵 drivers/ 树**（含 _template），而不是逐个型号。
# 注释行跳过：解释这条规则的那句话本身会提到物理名，命中它属于假报。
PHYS_RE='\bcan[0-9]+\b|/dev/tty[A-Za-z0-9]*|\bspi[0-9]+\b'
# 注释行不算违规：解释这条规则的那句话本身会提到物理名。
# 判据用 grep -oP 取「文件名:行号:内容」里的**内容部分**，再看它是否以注释符开头——
# 早先写成一条 -vE 交替式，其中 `\\*` 在 ERE 里是「零个或多个反斜杠」，
# 能匹配空串，于是把**每一行**都滤掉了（真缺陷被静默吞掉，连续两轮假绿）。
phys_hits="$(grep -rnE "$PHYS_RE" "$DRIVERS_DIR" 2>/dev/null \
             | grep -v '__pycache__' \
             | grep -vE ':[0-9]+:[[:space:]]*(//|#|/[*])' \
             | grep -vP '^[^:]+:[0-9]+:[[:space:]]*[*]' || true)"
if [ -n "$phys_hits" ]; then
  while IFS= read -r line; do
    violation "驱动目录出现物理标识（应只在 device/config/device.json）：$line"
  done <<< "$phys_hits"
fi
note "(e) 物理标识：扫描 $DRIVERS_DIR 全树（含 _template）"

# ---- 汇总 ----
TOTAL=$(( ${#DRIVER_MODELS[@]} ))
echo
if [ "$FAIL" -eq 1 ]; then
  echo "结果：发现违规（见上 [违规] 行）"
  exit 1
fi
echo "结果：$TOTAL 个驱动，五条断言全部通过"
exit 0
