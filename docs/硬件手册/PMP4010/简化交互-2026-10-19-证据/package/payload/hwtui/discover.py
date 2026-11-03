#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""km3 硬件工具发现器 —— 列出（并可选地运行）device/drivers/*/tools/*.py。

为什么是"发现"而不是"注册表"
    硬件会一直加：PMP4010 之后是毛刷、刮条、P65MAX-S，还有没写进清单的东西。
    每加一个硬件就改一次包装脚本、往一张表里补一行，是注定要漂移的做法。
    约定只有一条——**新硬件 = 新目录**：

        device/drivers/<型号>/tools/<工具>.py

    目录名用型号（厂家事实，见 docs/硬件手册/README.md 的目录约定），
    工具是独立可跑的 Python 脚本（`python3 该文件` 就能跑）。
    本文件不持有任何硬件清单，所以不存在"忘了登记"这回事。

    约定形状是 device/drivers/*/tools/*.py，刻意很窄：
    不递归、不认 <name>.sh、不认 device/src 下的东西。
    窄是为了让"为什么我的工具没被列出来"永远只有一个答案。

用法
    python3 tools/hwtui/discover.py --list          列出全部硬件工具
    python3 tools/hwtui/discover.py --list --json   机器可读
    python3 tools/hwtui/discover.py --path pmp4010/scan   只打印路径（给 shell 用）
    python3 tools/hwtui/discover.py --run scan -- --bus can-2   带环境运行（自动接好共用外壳）

    没有硬件工具时输出空列表并以 0 退出——这不叫失败，叫"还没写"。

不 import 被发现的工具
    摘要用 ast 从源码里取 docstring，绝不 import。原因有两条：
    一是工具可能 import 硬件库（pyserial / socketcan），本机不一定有；
    二是 import 会执行模块顶层代码，写文件、开总线这类副作用不该由"列个表"触发。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import runpy
import sys
from collections import namedtuple
from typing import Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "Tool",
    "discover",
    "repo_root",
    "hwtui_dir",
    "default_root",
    "format_table",
    "main",
]

#: 工具文件的相对位置：<drivers_root>/<型号>/tools/<工具>.py
DRIVERS_SUBDIR = os.path.join("device", "drivers")
TOOLS_SUBDIR = "tools"

#: 下划线开头的文件是内部模块（公共 helper、测试夹具），不是给用户直接跑的工具。
TOOL_PATTERN_SUFFIX = ".py"
SUMMARY_WIDTH = 58

Tool = namedtuple("Tool", "driver name path summary rel has_main")


def hwtui_dir() -> str:
    """本文件所在目录——也就是共用外壳 tui.py 的目录。"""
    return os.path.dirname(os.path.abspath(__file__))


def repo_root(start: Optional[str] = None) -> str:
    """从本文件往上找到仓库根。

    判据是 `device/` 与 `tools/` 同时存在——而不是"有 .git"，
    因为 .git 可能是 worktree 里的文件，也可能在导出源码里根本没有。
    """
    path = os.path.abspath(start or hwtui_dir())
    while True:
        if os.path.isdir(os.path.join(path, "device")) and os.path.isdir(os.path.join(path, "tools")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            # 找不到就退回本文件的祖父目录（tools/hwtui -> 仓库根），保证总有返回值。
            return os.path.abspath(os.path.join(hwtui_dir(), os.pardir, os.pardir))
        path = parent


def default_root(root: Optional[str] = None) -> str:
    """硬件工具根目录：<repo>/device/drivers。"""
    if root:
        return os.path.abspath(os.path.expanduser(root))
    return os.path.join(repo_root(), DRIVERS_SUBDIR)


def _summary(path: str) -> str:
    """取模块 docstring 的第一段（用 ast，不 import，见文件头）。

    读不到（非法 UTF-8、语法错、没有 docstring）就返回空串——
    坏文件仍然要被列出来，沉默地扫掉才是更糟的行为。
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return ""
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            source = raw.decode("utf-8", "replace")
        except Exception:
            return ""
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError):
        return ""
    doc = ast.get_docstring(module, clean=True) or ""
    if not doc:
        return ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _has_main(path: str) -> bool:
    """判断该工具被 `python3 <工具>.py` 直接跑时是否真的会做事。

    只用于显示（在列表里标一句 "no __main__"），但显示错了会误导人，
    所以判据要认全 Python 里真实存在的三种入口写法：

    1. `if __name__ == "__main__":` 保护块；
    2. 模块级 `argparse.ArgumentParser(...).parse_args()`——
       这是**最常见的**写法，早先只查前两种会把它误标成"没入口"；
    3. 顶层直接调用 `main()` / `run()`。

    用 ast 而不是字符串查找：字符串版会把注释里的 "def main("、
    docstring 里的 `--run` 示例全算成入口。解析失败（语法错、非法 UTF-8）
    时退回字符串粗查，宁可多标一个"有"也不要漏报。
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return True
    source = raw.decode("utf-8", "replace")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return "__main__" in source or "main(" in source

    for node in tree.body:
        # 1) if __name__ == "__main__":
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Compare) and any(
                isinstance(side, ast.Name) and side.id == "__name__"
                for side in [test.left] + list(test.comparators)
            ):
                return True
        # 2) 模块级调用：parse_args() / main() / run()
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in ("parse_args", "main", "run"):
                return True
    return False


def _scan_root(drivers_root: str, include_private: bool) -> List[Tool]:
    found: List[Tool] = []
    try:
        drivers = sorted(os.listdir(drivers_root))
    except OSError:
        # 目录不存在是正常状态（还没有硬件工具），不是错误。
        return found
    for driver in drivers:
        # 下划线开头是模板/内部目录（device/drivers/_template 就是脚手架），
        # 不是硬件。列进"硬件工具"会让人以为多了一个硬件存在。
        if driver.startswith(".") or (driver.startswith("_") and not include_private):
            continue
        tools_dir = os.path.join(drivers_root, driver, TOOLS_SUBDIR)
        if not os.path.isdir(tools_dir):
            continue
        try:
            names = sorted(os.listdir(tools_dir))
        except OSError:
            continue
        for name in names:
            if not name.endswith(TOOL_PATTERN_SUFFIX):
                continue
            if name.startswith("_") and not include_private:
                continue
            path = os.path.join(tools_dir, name)
            if not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, repo_root())
            found.append(Tool(
                driver=driver,
                name=name,
                path=os.path.abspath(path),
                summary=_summary(path),
                rel=rel,
                has_main=_has_main(path),
            ))
    return found


def discover(roots: Optional[Sequence[str]] = None, include_private: bool = False) -> List[Tool]:
    """列出全部硬件工具，按 (型号, 文件名) 排序。

    roots 为硬件工具根目录序列（每个下面直接是 `<型号>/tools/`）；
    省略则用 <repo>/device/drivers。传不存在的目录返回 []——
    新克隆还没有硬件工具时就是这个状态。
    """
    if roots is None:
        roots = [default_root()]
    out: List[Tool] = []
    for root in roots:
        out.extend(_scan_root(os.path.abspath(root), include_private))
    # 两个根指向同一份文件时（比如 symlink）去重，不然会列两遍。
    deduped: List[Tool] = []
    seen = set()
    for tool in sorted(out, key=lambda t: (t.driver, t.name, t.rel)):
        key = os.path.realpath(tool.path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(tool)
    return deduped


def _clip(text: str, width: int) -> str:
    """按显示列数截断并补 '…'。

    直接切片会把多字节摘要砍成半句，读的人会以为自己工具没写摘要——
    那是最浪费时间的一种"显示 bug 冒充内容 bug"。截断要用省略号说出来。
    """
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def format_table(tools: Sequence[Tool], root: str) -> str:
    lines = ["km3 hardware tools under %s" % root, ""]
    if not tools:
        lines.append("  (none)")
        lines.append("")
        lines.append("  add one as: %s/<model>/tools/<tool>.py" % DRIVERS_SUBDIR.replace(os.sep, "/"))
        return "\n".join(lines)
    lines.append("  %-14s %-22s %s" % ("DRIVER", "TOOL", "SUMMARY"))
    lines.append("  %s %s %s" % ("-" * 14, "-" * 22, "-" * SUMMARY_WIDTH))
    for tool in tools:
        note = "" if tool.has_main else "  (no __main__)"
        summary = _clip(tool.summary, SUMMARY_WIDTH)
        lines.append("  %-14s %-22s %s%s" % (tool.driver, tool.name, summary, note))
    lines.append("")
    lines.append("  %d tool(s). run one with: %s --run <tool> [-- args...]" % (
        len(tools), os.path.relpath(os.path.abspath(__file__), repo_root())))
    return "\n".join(lines)


def _as_json(tools: Sequence[Tool], root: str) -> str:
    payload = {
        "root": root,
        "count": len(tools),
        "tools": [
            {"driver": t.driver, "name": t.name, "path": t.rel, "summary": t.summary, "main": t.has_main}
            for t in tools
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _select(tools: Sequence[Tool], needle: str) -> List[Tool]:
    """按 "型号/文件名"、"型号"、文件名、"型号/其他子串" 依次匹配，均不区分大小写。"""
    want = needle.strip().lower()
    exact = [t for t in tools if t.name.lower() == want or ("%s/%s" % (t.driver, t.name)).lower() == want]
    if exact:
        return exact
    by_driver = [t for t in tools if t.driver.lower() == want]
    if by_driver:
        return by_driver
    partial = [t for t in tools if want in ("%s/%s" % (t.driver, t.name)).lower()]
    if partial:
        return partial
    # 允许省略 .py 后缀
    stem = [t for t in tools if os.path.splitext(t.name)[0].lower() == want]
    return stem


def run_tool(tool: Tool, argv: Sequence[str]) -> int:
    """在"共用外壳可见 + 工具目录在 sys.path[0]"的环境里运行工具。

    接好这两条路径的原因：工具的 import 习惯应当是 `import tui` 和
    同目录的 helper，而不是 `from tools.hwtui import tui`——
    后者要求调用方永远从仓库根跑，而工程师会在任何地方跑它。
    """
    tool_dir = os.path.dirname(tool.path)
    extra = os.pathsep.join([tool_dir, hwtui_dir()])
    env_path = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = extra + (os.pathsep + env_path if env_path else "")
    # 让工具自己的目录优先于仓库根，避免撞上同名模块。
    sys.path.insert(0, tool_dir)
    sys.path.insert(0, hwtui_dir())
    saved_argv = sys.argv
    sys.argv = [tool.path] + list(argv)
    try:
        runpy.run_path(tool.path, run_name="__main__")
        return 0
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1
    finally:
        sys.argv = saved_argv


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="discover.py",
        description="列出（并可选运行）device/drivers/<型号>/tools/*.py 下的 km3 硬件工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="新硬件不需要新的包装脚本：加一个 device/drivers/<型号>/tools/<工具>.py 即可。",
    )
    ap.add_argument("--list", action="store_true", help="列出全部硬件工具（省略动作时默认做这个）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（给其他工具消费）")
    ap.add_argument("--root", default=None,
                    help="硬件工具根目录（默认 <repo>/device/drivers）")
    ap.add_argument("--all", action="store_true", help="连下划线开头的内部模块一起列出")
    ap.add_argument("--path", metavar="TOOL", help="只打印匹配工具的相对路径（供 shell 使用）")
    ap.add_argument("--run", metavar="TOOL", help="接好共用外壳路径后运行该工具")
    ap.add_argument("--dry-run", action="store_true", help="配合 --run：只打印将执行什么，不真的执行")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = build_parser()
    args, extra = ap.parse_known_args(argv)
    root = default_root(args.root)
    tools = discover([root], include_private=args.all)

    if args.run:
        matches = _select(tools, args.run)
        if not matches:
            print("no hardware tool matches %r; add %s/<model>/tools/%s.py" % (
                args.run, DRIVERS_SUBDIR.replace(os.sep, "/"),
                os.path.splitext(os.path.basename(args.run))[0]), file=sys.stderr)
            return 2
        if len(matches) > 1:
            print("%r matches %d tools: %s" % (
                args.run, len(matches), ", ".join("%s/%s" % (t.driver, t.name) for t in matches)),
                file=sys.stderr)
            return 2
        tool = matches[0]
        run_args = [a for a in extra if a != "--"]
        if args.dry_run:
            print("would run: python3 %s %s" % (tool.rel, " ".join(run_args)))
            print("with PYTHONPATH=%s%s%s" % (
                os.path.dirname(tool.path), os.pathsep, hwtui_dir()))
            return 0
        return run_tool(tool, run_args)

    if args.path:
        matches = _select(tools, args.path)
        if not matches:
            return 1
        for tool in matches:
            print(tool.rel)
        return 0

    # 默认（以及显式 --list）：列出来。stdout 保持干净，好让 --json / 管道可用。
    root_label = os.path.relpath(root, repo_root()) if os.path.isdir(root) else root
    if args.json:
        print(_as_json(tools, root_label))
    else:
        print(format_table(tools, root_label))
        if not tools:
            # 提示走 stderr：stdout 的"空列表"是要被机器读的。
            print("note: no hardware tools found under %s (exit 0)" % root, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
