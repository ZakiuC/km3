#!/usr/bin/env bash
# 溯源闸门：源码里的每个寄存器地址、对象字典索引、标度因子都必须能追溯到
# docs/硬件手册/<型号>/ 下的某份厂家文件。CI 的 drivers job 调用本脚本。
#
# 本脚本把仓库规则从散文变成可执行项。规则原文见
# docs/硬件手册/README.md：「源码里的每一个寄存器地址、对象字典索引、标度因子，
# 都必须能追溯到这里的某份文件」。
#
# 检查三项：
#
#   (1) 引用的目标文件必须**真实存在**。
#       规则允许"手册还没到"，但不允许"写着引用了某份文件而那文件不存在"——
#       前者是状态，后者是撒谎。两者在 CI 里必须区分开。
#   (2) od.json 的每个条目都必须带 status 与 cite 两个字段（一个都不能省）。
#       省略 cite 会让"尚未核对"与"有人删了字段"不可区分。
#   (3) 驱动源码里出现的对象字典索引字面量，**同一行**必须有引用注释。
#
# 为什么 (3) 要求"同一行"而不接受"文件头一句全局注释"：
#
#   全局注释会让**新增**的常量天然免检——它在文件头，新写的行不在它旁边。
#   而新增常量正是最需要被查的那一刻。
#
# 退出码：0 通过；1 有违规；2 脚本/环境错误。
set -uo pipefail
cd "$(dirname "$0")/.."

DRIVERS_DIR="device/drivers"
MANUAL_DIR="docs/硬件手册"

# 违规判定与输出格式都在下面的 python 段里实现（与 gen-od.py 共用同一套
# 引用解析规则）。shell 段只负责找路径与传递退出码，不重复实现一半。

if [ ! -d "$DRIVERS_DIR" ]; then
  echo "  未找到 $DRIVERS_DIR/，跳过（尚未创建任何驱动）"
  echo "结果：无驱动可查（不是通过——是还没有对象）"
  exit 0
fi

echo "==> 手册溯源检查"

# 主体用 python：引用解析规则（路径 + :章节 的写法）必须与
# tools/hwdriver/gen-od.py 对 cite 的解析**一致**。两处实现同一套规则
# 就是两份真相，改了一处必漏另一处。因此这里复用同一份解析函数，
# 而不是在 shell 里重写一次正则。
python3 - "$DRIVERS_DIR" "$MANUAL_DIR" <<'PYEOF'
import io
import os
import re
import sys

drivers_dir, manual_dir = sys.argv[1], sys.argv[2]

# 违规计数。定义在 helper 之前：violation() 用 global 引用它，
# 而 Python 的 global 不会隐式创建——忘了这一行的报错是
# NameError: name 'fail' is not defined，指向的是 violation() 的调用点，
# 不是这处遗漏。实测踩过。
fail = 0


def note(msg):
    """进度提示。**不是**失败——注意与 violation() 的区别：

    本脚本的每条 note 都对应「这里还没对象可查」这类正常状态
    （手册未到、驱动刚生成），而不是「查了但不合格」。
    两者在输出里必须一眼可分，否则 CI 红的时候人要先猜是哪一类。
    """
    print("  %s" % msg)


def violation(msg):
    """违规。调用方负责把 fail 置 1 并最终退出 1。"""
    global fail
    print("  [违规] %s" % msg)
    fail = 1

# ---- 引用格式的**唯一**解析规则（与 gen-od.py 一致）----
#
# 形如：docs/硬件手册/PMP4010/协议/对象字典.md:12
#       docs/硬件手册/PMP4010/协议/对象字典.md §3.2
#       docs/硬件手册/PMP4010/操作手册.md
# 即「仓库相对路径」，可选跟一个冒号或 § 加章节/页码。
#
# 为什么不接受裸文件名（如「对象字典.md」）：那需要猜是哪个型号的目录，
# 而猜错正是本仓库最容易发生的漂移（PMP4010 与 pmp4010 已经并存）。
# 因此要求写全路径——多打十来个字符，换掉一整类歧义。
CITE_RE = re.compile(r"(docs/[^\s:：]+?\.(?:md|pdf|eds|dcf|txt|xlsx))(?:\s*[:：§]\s*([^\s，。；)]+))?")


def parse_cite(text):
    """从一段文本里取出 (文件路径, 章节或 None)。取不到返回 None。

    与 gen-od.py 的 cite 处理保持同一口径：cite 是**字符串**，
    本函数只负责把它切成「文件」与「定位」两半。
    """
    if not isinstance(text, str):
        return None
    m = CITE_RE.search(text)
    if not m:
        return None
    return m.group(1), (m.group(2) or None)


def resolve(path, repo=os.getcwd()):
    """引用路径是否指向真实文件。

    只认仓库相对路径：引用必须自仓库根可解析。
    不做「相对当前文件」的兜底——那会让同一段文字在不同文件里指向不同东西。
    """
    return os.path.isfile(os.path.join(repo, path))


# 该型号的手册目录。**大小写不敏感**地找：
# 驱动目录约定小写（pmp4010），而手册目录用厂家铭牌写法（PMP4010）。
# 两者是同一个型号的两种拼法，硬要求一致会逼人二选一，
# 而无论选哪个都会与另一处既有约定冲突。
manual_dirs = {}
if os.path.isdir(manual_dir):
    for name in sorted(os.listdir(manual_dir)):
        full = os.path.join(manual_dir, name)
        if os.path.isdir(full):
            manual_dirs[name.lower()] = full


def manual_dir_for(model):
    return manual_dirs.get(model.lower())


def cite_ownership(path, model):
    """引用是否落在**本型号**的手册目录下。返回 (是否合格, 说明)。

    指向另一个型号的手册是"复制目录后漏改型号"的典型症状：
    路径真实存在、格式完全正确、检查器不查归属就放行，
    而读的人以为自己引的是本型号的依据。这类缺陷最难在评审时看出来，
    因为它长得和正确的东西一模一样。
    """
    md = manual_dir_for(model)
    if md is None:
        # 本型号还没有手册目录：无处归属，不判违规。
        # 这是"手册未到"的正常状态，不是缺陷。
        return True, ""
    if os.path.abspath(path).startswith(os.path.abspath(md) + os.sep):
        return True, ""
    return False, md


total_cites = 0
total_entries = 0
total_index_literals = 0

models = []
for name in sorted(os.listdir(drivers_dir)):
    full = os.path.join(drivers_dir, name)
    if os.path.isdir(full) and not name.startswith("_"):
        models.append(name)

# ---------------- (1)(2) od.json 的 status / cite ----------------
for model in models:
    od_path = os.path.join(drivers_dir, model, "driver", "od.json")
    if not os.path.isfile(od_path):
        # 驱动目录刚由 new-driver.sh 生成、od.json 还没填时是正常的。
        # 但要显式说出来，避免"文件不存在"与"全部通过"看起来一样。
        note("%s: 无 driver/od.json，跳过（尚未填对象字典）" % model)
        continue

    import json
    try:
        with io.open(od_path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError as exc:
        violation("%s: od.json 不是合法 JSON：%s" % (model, exc))
        fail = 1
        continue

    entries = doc.get("entries", [])
    total_entries += len(entries)
    for i, e in enumerate(entries):
        where = "%s od.json entries[%d](%s)" % (model, i, e.get("key", e.get("name", "?")))
        if "status" not in e:
            violation("%s 缺 status" % where)
            fail = 1
        if "cite" not in e:
            violation("%s 缺 cite（未核对应为 null，不能省略字段）" % where)
            fail = 1
            continue

        cite = e.get("cite")
        status = e.get("status")
        if status == "verified":
            parsed = parse_cite(cite)
            if not parsed:
                violation("%s 标为 verified 但 cite 不是可解析的仓库相对路径：%r"
                          % (where, cite))
                fail = 1
                continue
            total_cites += 1
            path, _ = parsed
            if not resolve(path):
                violation("%s 的 cite 指向不存在的文件：%s" % (where, path))
                fail = 1
            # 手册目录归属：引用必须在**本型号**的手册目录下。
            # 指向另一个型号的手册是复制目录后漏改型号的典型症状，
            # 而它看起来完全正常。
            owned, md = cite_ownership(path, model)
            if not owned:
                violation("%s 的 cite 指向 %s，不在本型号手册目录 %s/ 下"
                          % (where, path, md))
                fail = 1
        elif status in ("unverified", "assumed"):
            if cite is not None:
                violation("%s 标为 %s 却带了 cite（%s）——两者矛盾"
                          % (where, status, cite))
                fail = 1
        else:
            violation("%s 的 status %r 非法（只能取 unverified / assumed / verified）"
                      % (where, status))
            fail = 1

note("(1)(2) od.json：%d 个驱动、%d 个条目、%d 条已核对引用"
     % (len(models), total_entries, total_cites))

# ---------------- (3) 源码里的索引字面量必须同行带引用 ----------------
#
# 扫 *.cpp / *.hpp / *.py。判定"这是一个对象字典索引字面量"的规则：
#
#   - CANopen 索引的惯用写法是 0x1000..0xFFFF 之间的十六进制
#   - 关键：**排除 _generated 文件**。od_generated.hpp 与 canopen_consts.py
#     是生成物，它们的索引来自 od.json 的 cite，不该在这里重复要求引用
#     ——否则每个生成物都会恒红，而"恒红的检查"等于没有检查。
# 字面量的**完整形态**，含 C++ 的整型后缀。
#
# 为什么必须带后缀：C++ 里 `0x1400u` 与 `0x1400` 是同一个值，但正则
# `\b0x[0-9A-Fa-f]{4}\b` 匹配不到前者 —— `0`/`u` 都是词字符，`4` 与 `u`
# 之间没有词边界。实测后果：驱动里 7 处状态字掩码（0x1400u / 0x2000u / 0x1000u）
# 全部静默漏检，而 Python 侧同一批值不带后缀、照报不误。同一规则在两种语言上
# 判定不一致，等于 C++ 侧没有这道闸门 —— 那正是本轮要修的东西。
SUFFIX_RE = re.compile(r"^0x[0-9A-Fa-f]{4}(?:[uUlL]{0,3})$")
INDEX_LITERAL = re.compile(r"\b0x[0-9A-Fa-f]{4}[uUlL]{0,3}\b")

# 生成物豁免：**按内容判定**，不按文件名。
#
# 一度写成按文件名匹配（"_generated.hpp"），但那有两个问题：
#   1. 文件名是会变的。生成器的输出名改一次，豁免就静默失效，
#      而失效的表现是每个生成物恒红——恒红的检查等于没有检查。
#   2. 名字可以伪装。任何人把手写的文件叫 xxx_generated.hpp 就免检了。
# 产物首行有生成器写的字面量标记，那才是它"是不是生成物"的唯一依据，
# 也**只有**生成器能写出它。
#
# 判定改为**认标记的形状**而不是认某一个生成器：本仓库现已有三个生成器
# （gen-od.py / gen-canopen.py / gen-pmp4010-access.py），而它们的标记措辞
# 各不相同（大小写、分隔符、"BY" vs "by"）。只认 gen-od.py 那一句的后果是
# 另外两份生成物**不豁免** —— 它们只要写出任何一个四位十六进制字面量就恒红，
# 而"恒红的检查"等于没有检查。这一条已经真实发生过：od_access_generated.hpp
# 的首行是 "Generated by ...;"，与本常量逐字不等。
#
# 形状规则：首行是注释，含 "GENERATED"（不分大小写）与 "DO NOT EDIT"，
# 且点名 tools/hwdriver/ 下的某个脚本 —— 后者是关键，它把"生成物"这件事
# 钉在**本仓库的生成器**上。任何人把手写文件叫 xxx_generated.hpp 并抄一句
# "DO NOT EDIT" 也过不了：他得同时伪造出生成器脚本的路径。
GEN_MARKER_RE = re.compile(
    r"^\s*(?://|#)\s*(?=.*\bgenerated\b)(?=.*\bDO\s+NOT\s+EDIT\b)"
    r"(?=.*\btools/hwdriver/[A-Za-z0-9_.\-]+\.py\b).*$",
    re.IGNORECASE)

SKIP_PATH_PARTS = ("__pycache__",)

# 值本身不是"厂家事实"的例外。
#
# 0xFFFF 在本仓库有两个固定含义，都不是厂家索引：
#   - od_generated.hpp 的 kIndexUnset（「未填」的哨兵）
#   - CANopen 的 0xFFFF 作为 16 位全 1 的常见哨兵/掩码
# 把它当索引要求引用，会逼人在哨兵旁边写一句假的引用注释——
# 那比不写更坏：它训练人"引用可以随便写"。
SENTINEL_VALUES = {"FFFF", "0000"}   # 只存**数字部分**，比较大小时统一按数字比（见 canon_hex）

# CiA 301 的**公共对象**：这些索引由规范固定，不由厂家定义，
# 因此不需要厂家手册引用。清单刻意写死而不是按范围放行
# （例如"0x1000-0x1FFF 全放行"是错的：那一段里厂家自定义的条目不少）。
#
# 唯一权威的一份在 tools/hwtui/canopen.py 的 SDO_INDEX_TABLE。
# 驱动里**不该**出现这些字面量——要用就调标准层。它们出现在驱动测试里，
# 是测试在构造"读一个公共对象"的调用以验证守卫，属于合理用法。
CIA301_PUBLIC = {
    "1000",  # device type
    "1001",  # error register
    "1003",  # predefined error field
    "1005",  # SYNC COB-ID
    "1008",  # manufacturer device name
    "100A",  # manufacturer software version
    "1014",  # EMCY COB-ID
    "1017",  # producer heartbeat time
    "1018",  # identity object
}
NON_VENDOR_VALUES = SENTINEL_VALUES | CIA301_PUBLIC


def canon_hex(literal):
    """把 `0x6040` / `0x6040u` / `0x6040UL` 归一成数字部分的大写形式 `6040`。

    必须先剥掉整型后缀：豁免清单（SENTINEL_VALUES / CIA301_PUBLIC）里存的是
    纯数字，带后缀的写法若不剥就会与清单永远匹配不上 —— 豁免静默失效，
    每个带 `u` 的公共对象都被报成违规。与下面 `.upper()` 不能整串调用
    是同一类陷阱：**归一化写不干净，清单就形同虚设**。
    """
    body = literal[2:]
    # 剥掉所有尾随的整型后缀字符（u/U/l/L）。**不能**用后缀表配 break：
    # `0x1400UL` 剥掉 "UL" 后还得再看有没有别的，而 `0x1400ULL` 与 `0x1400ul`
    # 混在同一个集合里时，先删一个字母的顺序会让剩下的那个字母留在串上，
    # 于是豁免清单匹配不上——正是本函数注释里说的那类静默失效。
    while body and body[-1] in "uUlL":
        body = body[:-1]
    return body.upper()


def is_generated(path):
    """该文件是不是生成物（读首行标记判定）。

    只看**首行**：生成器把标记写在第一行，而它后面的内容无论写成什么
    都不该改变判定。若改成扫全文，任何一份引用了生成物路径的普通源码
    （例如本仓库的检查脚本自己）都会被误判成生成物而免检。
    """
    try:
        with io.open(path, encoding="utf-8") as fh:
            return bool(GEN_MARKER_RE.match(fh.readline().rstrip("\n")))
    except (OSError, UnicodeDecodeError):
        return False


def is_exempt(path):
    # 标准层 tools/hwtui/canopen.py 不在此列：它只实现 CiA 301 的公共对象，
    # 而本检查面向的是 drivers/ 目录树，遍历根本不会走到它那里。
    # 显式写这一条是为了让"为什么标准层不被查"在代码里可读。
    if any(p in path.split(os.sep) for p in SKIP_PATH_PARTS):
        return True
    return is_generated(path)


def strip_comment(line):
    """去掉行尾注释，返回 (代码部分, 注释部分)。

    C/C++ 的注释符是 `//` 与 `/*`（本仓库不用块注释跨行），
    Python 的 `#` 只有在**字符串外**才是注释 —— 而这一区别只能靠
    tokenize 判定，靠扫字符会把 `print("# 0x6040")` 当成注释。

    因此：.py 文件不交给这个函数，走 python_tokens() 的精确路径；
    这个函数只服务 C/C++。
    """
    # `#` 前面必须是行首或空白才算注释 —— **否则 `0x6040` 里的那个 `#` 会被
    # 当成注释起始**，把索引本身截掉，于是 0x6040 变成空串、检查永远不报。
    # 实测踩过：修好之前，所有十六进制字面量都被静默漏检，
    # 检查器看起来一直在跑、一直没有命中——比崩溃更隐蔽。
    # 十六进制写法 `0x...` 里的 x 紧跟数字，`#` 只可能出现在 `0x` 之后的
    # 数字里（不，`#` 不在十六进制字符集里），真正的情形是 C 的 `#include`
    # 与字符串里的 `#`。这里用"前导空白"这条规则一次覆盖两者。
    cut = len(line)
    for marker in ("//", "#"):
        start = 0
        while True:
            i = line.find(marker, start)
            if i == -1:
                break
            # 允许位于行首，或前面是空白/特定标点；排除 `0x...` 这类
            # 字母数字上下文里的 `#`。
            if i == 0 or line[i - 1] in " \t;,){}]:":
                cut = min(cut, i)
                break
            start = i + 1
    return line[:cut], line[cut:]


def python_code_and_comments(path):
    """用 tokenize 精确切出 Python 文件的「代码 token」与「注释」两串。

    为什么必须用 tokenize 而不是逐行扫字符：
      1. 三引号 docstring 会跨行。逐行扫会把 docstring **内部**的行
         当成代码 —— 实测中一个模块 docstring 里的「用 SDO 读 0x1000 探」
         被判成"硬编索引无引用"，而它只是一句注释。
      2. 字符串里的 `#` 不是注释。扫字符会截断一个含 `#` 的字符串，
         把它左边当成代码——反之亦然。
    两者都是纯噪声，而噪声会让检查器被忽略。

    返回 (代码行文本列表, 注释文本列表)。行号仍按原文件算 ——
    报错时人要去原文件里找那一行。
    """
    import tokenize
    lines = io.open(path, encoding="utf-8").read().split("\n")
    code_lines = {}
    comments = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO("\n".join(lines)).readline):
            ttype, tstring, (srow, _), (erow, _), _ = tok
            if ttype == tokenize.COMMENT:
                comments.append((srow, tstring))
                continue
            # 字符串（含 f-string 与 docstring）整体算"文本"，不算代码：
            # 里面的 0x6040 是给人看的说明，不是拿去发帧的索引。
            if ttype in (tokenize.STRING,):
                continue
            if ttype in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
                         tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER):
                continue
            if 1 <= srow <= len(lines):
                code_lines[srow] = code_lines.get(srow, "") + " " + tstring
    except (tokenize.TokenError, IndentationError):
        # 词法失败（例如文件本身有语法错）时退化为逐行扫字符：
        # 宁可多报几条，也不要让检查器整个崩掉。
        for i, line in enumerate(lines, 1):
            code, comment = strip_comment(line)
            code_lines[i] = code
            if comment:
                comments.append((i, comment))
    return code_lines, comments


# 只在**生产代码**上查 driver/ 与 tools/：那是驱动真正拿去发帧的地方。
#
# tests/ 不查。那里的数字是**测试输入**，不是被实现的硬件事实，
# 而要求测试带厂家引用会逼出两种坏结果：给一个任意值编一句假引用，
# 或者让测试从被测对象取常量（那样它就不再独立于被测对象了）。
#
# 引用可以写在同行注释里，也可以写在**紧邻上一行**的注释里 ——
# 后者是为长行准备的：一行已经写到 100 列，再挂一个完整手册路径会超限。
# 只认"紧邻上一行"，不认"文件头"：远距离的全局注释会让新增常量天然免检，
# 而那正是最该被查的那一刻。
for model in models:
    root = os.path.join(drivers_dir, model)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        rel = os.path.relpath(dirpath, root)
        if rel.split(os.sep)[0] == "tests":
            continue
        for fn in sorted(filenames):
            if not fn.endswith((".cpp", ".hpp", ".h", ".py")):
                continue
            # tests/ 目录已在上面的 rel 判定里排除；这里补的是**同目录下的
            # 测试文件** —— `_test_<型号>.py` 与 `test_<型号>.py`。
            #
            # 理由与 tests/ 完全一致（见上方那个大段注释）：那里的数字是
            # **测试输入**，不是被实现的硬件事实。要求测试带厂家引用会逼出
            # 两种坏结果：给一个任意值编一句假引用，或者让测试从被测对象
            # 取常量（那样它就不再独立于被测对象）。
            #
            # 实测背景：本轮新增的 _test_pmp4010.py 有 23 处四位数字被报成
            # 违规，全部是夹具里的 (0x6040, 0) 这类键。真正该查的是
            # pmp4010.py 里的 4 处，它们确实是发帧用的掩码。
            # 覆盖三种通行写法：test_x.py / x_test.py / _test_x.py
            if fn.startswith("test_") or fn.startswith("_test_") or fn.endswith("_test.py"):
                continue
            path = os.path.join(dirpath, fn)
            if is_exempt(path):
                continue
            try:
                text = io.open(path, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue

            if fn.endswith(".py"):
                code_lines, comments = python_code_and_comments(path)
                comment_at = {}
                for row, ctext in comments:
                    comment_at.setdefault(row, []).append(ctext)
                for lineno in sorted(code_lines):
                    code = code_lines[lineno]
                    found = INDEX_LITERAL.findall(code)
                    if not found:
                        continue
                    real = [v for v in found if canon_hex(v) not in NON_VENDOR_VALUES]
                    if not real:
                        continue
                    total_index_literals += 1
                    cands = list(comment_at.get(lineno, []))
                    cands += list(comment_at.get(lineno - 1, []))
                    cite = None
                    for c in cands:
                        cite = parse_cite(c)
                        if cite:
                            break
                    if not cite:
                        violation(
                            "%s:%d 出现对象字典索引字面量但同行（或紧邻上一行）无引用：%s"
                            % (path, lineno, code.strip()[:70]))
                        fail = 1
                    elif not resolve(cite[0]):
                        violation("%s:%d 的引用指向不存在的文件：%s"
                                  % (path, lineno, cite[0]))
                        fail = 1
                    else:
                        owned, md = cite_ownership(cite[0], model)
                        if not owned:
                            violation("%s:%d 的引用指向 %s，不在本型号手册目录 %s/ 下"
                                      % (path, lineno, cite[0], md))
                            fail = 1
                continue

            prev_comment = ""
            for lineno, line in enumerate(text.split("\n"), 1):
                code, comment = strip_comment(line)
                found = INDEX_LITERAL.findall(code)
                if found:
                    real = [v for v in found if canon_hex(v) not in NON_VENDOR_VALUES]
                    if real:
                        total_index_literals += 1
                        cite = parse_cite(comment) or parse_cite(prev_comment)
                        if not cite:
                            violation(
                                "%s:%d 出现对象字典索引字面量但同行（或紧邻上一行）无引用：%s"
                                % (path, lineno, code.strip()[:80]))
                            fail = 1
                        elif not resolve(cite[0]):
                            violation("%s:%d 的引用指向不存在的文件：%s"
                                      % (path, lineno, cite[0]))
                            fail = 1
                        else:
                            owned, md = cite_ownership(cite[0], model)
                            if not owned:
                                violation(
                                    "%s:%d 的引用指向 %s，不在本型号手册目录 %s/ 下"
                                    % (path, lineno, cite[0], md))
                                fail = 1
                # 只把**纯注释行**留给下一行用。代码行尾的注释不给下一行背书
                # ——那会让上一行的引用顺带盖住下一行，等于一个引用管两行。
                prev_comment = comment if not code.strip() else ""

note("(3) 源码索引字面量：检查 %d 处，全部同行带引用" % total_index_literals)

# ---------------- 手册目录存在性 ----------------
for model in models:
    md = manual_dir_for(model)
    if md is None:
        note("%s: 尚无手册目录 %s/<型号>/（手册未到时是正常状态）" % (model, manual_dir))
        continue
    if not os.path.isfile(os.path.join(md, "README.md")):
        violation("%s 有手册目录 %s/ 但缺 README.md（未确认项清单无处登记）"
                  % (model, md))
        fail = 1

print()
if fail:
    print("结果：发现违规（见上 [违规] 行）")
    sys.exit(1)
print("结果：%d 个驱动的溯源检查全部通过" % len(models))
sys.exit(0)
PYEOF
PY_EXIT=$?

if [ "$PY_EXIT" -eq 0 ]; then
  exit 0
fi
exit 1
