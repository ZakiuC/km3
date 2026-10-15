#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PMP4010（机身抬降电机 A）板载调试工具 —— curses TUI 与一次性子命令。

运行环境是板子本身（TL3588，python3 3.10.12，curses / can-utils 齐备，
tput colors=256），不是开发机。三条前提都来自实测，工具的形态是被它们决定的：

1. **km3 用户 CapEff=0**：`ip link set` 报 Operation not permitted，
   `systemctl restart` 报 Interactive authentication required。
   因此本工具**永不自己执行**这两类命令：它只改自己有权改的文件，
   然后把逐字可复制的 root 命令打到屏上，由人执行。
   工具的职责边界是"把现状摆清楚 + 写好配置"，不是"替人提权"。

2. **接口状态归 km3-can.service 所有**：内核接口的 up/down 与位速率由
   systemd 那个单元在启动时设置，本工具不接管。绕过单元自己 `ip link set`
   会造出一个"看起来好了、下次重启又变回去"的假象。

3. **物理标识只在 device.json**：本文件里不出现任何具体的内核接口名。
   逻辑名 `can-2` 到物理接口的映射唯一来源是
   device/config/device.json 的 bus.can 数组。工具从那里读，写死一个名字
   就等于在第二个地方维护同一份事实。

子命令分三类，准入条件不同：

  离线或无发帧检查（不受 km3-rt-controller 状态限制）
    --test       严格离线自检（配置 / 声明 / 标准层 MockBus，不开真实套接字）
    --od-dump    打印对象字典声明与核对状态
    --bus-stats  接口现状、netlink CAN 专用错误计数、kmod、实时进程状态
    --raw        只监听，抓原始帧（不发帧）

  只读**但发帧**（只发 SDO 上传请求，零写请求；active 时要求 --i-know-rt-is-running）
    --scan       SDO 扫节点（1..127 或 --node-range），--identity 读身份四项

  写文件（不发帧，因此**不受** OD 闸门与实时进程确认限制）
    --init       三段式：读现状 -> 写 /etc/km3/can.env -> 打印 root 命令（见下）

  运动类（OD 闸门 + active 时要求 --i-know-rt-is-running）
    --nmt        发 NMT 命令
    --motion     CiA402 使能 / 失能 / 快停 / 回零

三条准入规则，顺序固定：
  1. **对象字典闸门**（refuse_motion）：od.json 里只要还有 status != verified 的条目，
     运动类子命令打印「N 项对象字典未核对」并拒绝。只警告不拒绝是不够的 ——
     动错方向的电机不会因为屏幕上有一行黄字就停下来。
     闸门在**发帧确认之前**开口：否则"点个头就能动"的错误暗示先于真实阻塞出现。
  2. **标准层**（require_std_layer）：总线操作只走 tools/hwtui，缺失时拒绝。
  3. **实时进程确认**（require_rt_ack）：km3-rt-controller active 时它正在用这条总线，
     两条控制流同时持有同一会话，只能由人确认。状态取不到时按更严一侧处理。

--init 是三段式，**顺序不可颠倒**，每段都对应一条实测事实：
  (1) 读 /etc/km3/device.json 取 can-2 的 iface 与 bitrate，再用
      `ip -details link show` 读现状，不一致就报差异（只报告，不改）；
  (2) 写目标位速率到 /etc/km3/can.env 的 CAN_BITRATE=（km3 拥有该文件，
      0640 km3:km3，无需提权）；
  (3) 打印逐字可复制的 `sudo systemctl restart km3-can`，并说明原因。
--init --verify 是纯只读的：做 (1) 与 (3) 的展示，不做 (2)。
"""

import argparse
import json
import os
import re
import math
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEVICE_JSON = "/etc/km3/device.json"
# 兜底顺序与模板 device/drivers/_template/tools/<代号>.py 的
# _DEVICE_JSON_CANDIDATES 一致：板端置备路径 -> 板上部署路径 -> 仓库内模板。
# 仓库内那一份是给未置备的开发机用的；缺了它，本工具在开发机上连 --help
# 之外的任何只读子命令都跑不了，于是它在被部署到板上之前没人能验证。
DEVICE_JSON_FALLBACKS = [
    "/opt/km3/current/device.json",
    os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "config", "device.json")),
]
CAN_ENV = "/etc/km3/can.env"
CAN_SERVICE = "km3-can.service"
RT_SERVICE = "km3-rt-controller.service"

# 逻辑名。CAN-2 = 主板第二路 CAN —— 逻辑名与内核接口名"下标差一位"，
# 正是这一层存在的理由，所以这里只写逻辑名。
LOGICAL_BUS = "can-2"

ANSI = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m", "cyan": "\033[36m",
}


# ---------------------------------------------------------------------------
# 标准层（tools/hwtui/）的接入
# ---------------------------------------------------------------------------
#
# 规格（docs/架构设计/硬件驱动开发路径.md 第 7 步）要求：CANopen 的帧组装、
# SDO 收发、NMT 状态机**只在** tools/hwtui/canopen.py 实现一次，驱动工具只调用它。
# 因此本文件不自己拼 NMT 帧、不自己算 COB-ID。
#
# 但本工具必须**在没有 hwtui 的机器上也能 --help / --od-dump / --init --verify**，
# 否则一个还没写完的标准层会把整个工具卡死。因此：
#   - 顶层不 import hwtui（ImportError 会让 argparse 都跑不起来）；
#   - 按需导入；总线操作在标准层缺失时明确拒绝，离线展示继续可用。
_HWTUI_ERROR = None
_canbus = None
_canopen = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))

# 标准层可能以两种形态出现，两种都要能找到：
#   1) 仓库里：<repo>/tools/hwtui/*.py  -> 用包内相对导入（hwtui.canbus）
#   2) 板上部署后：<root>/<ver>/hwtui/*.py 与 <root>/<ver>/device/drivers/*/tools/*.py
#      —— tools/deploy_hwtools.sh 就是这么摆的，而 hwtui 内部的 import 是**扁平**的
#      （canbus.py 里写的是 `from canopen import ...`），因此那时真正需要
#      在 sys.path 上的是 hwtui/ 目录本身，不是它的父目录。
#
# 曾经只加了前两者，结果标准层被判为不可用、工具悄悄走降级实现 ——
# 而"悄悄"是这里最糟的部分：使用者以为在用被审计过的标准层。
_STD_CANDIDATES = (
    _REPO_ROOT,
    os.path.join(_REPO_ROOT, "tools"),
    os.path.join(_REPO_ROOT, "tools", "hwtui"),
    os.path.join(_HERE, "..", "..", "hwtui"),
    os.path.join(os.path.dirname(_HERE), "..", "..", "hwtui"),
)
for _p in _STD_CANDIDATES:
    _p = os.path.abspath(_p)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)   # append 而非 insert：不要遮蔽调用方自己的模块


def _load_std_layer():
    """按需导入 tools/hwtui 的标准层。返回 True 表示可用。

    优先扁平模块（canbus/canopen），与共用库内部导入保持同一模块身份；
    再兼容 hwtui 包形式。
    """
    global _canbus, _canopen, _HWTUI_ERROR
    if _canbus is not None and _canopen is not None:
        return True
    errors = []
    for importer in ("flat", "package"):
        try:
            if importer == "package":
                from hwtui import canbus as _cb, canopen as _co
            else:
                import canbus as _cb, canopen as _co       # noqa: PLC0415
        except ImportError as exc:
            errors.append("{}: {}".format(importer, exc))
            continue
        _canbus, _canopen = _cb, _co
        return True
    _HWTUI_ERROR = "；".join(errors)
    return False


def std_layer_available():
    return _load_std_layer()


# ---------------------------------------------------------------------------
# 对象字典的核对状态：运动类子命令的闸门
# ---------------------------------------------------------------------------
#
# 单一事实来源是 driver/od.json。**不硬编码条数，也不解析 od_generated.hpp**：
# 前者会与 od.json 漂移，后者是生成物、字段会随生成器改。
# 判据与 C++ 侧的 od::kOdVerified 必须同源，否则工具说"可以动"、驱动说
# "编不过"，两边都以为自己是对的（模板 tools/<代号>.py 的 od_status() 是同一套判据，
# 那份是蓝本，本文件照它来）。
#
# --nmt / --motion 统一先过 refuse_motion()，随后才检查标准层与实时进程确认。
# 对象字典尚未核对时，连真实总线套接字也不打开。
# 规则本身来自 CLAUDE 侧要求（模板文件头）：只警告不拒绝是不够的，
# 动错方向的电机不会因为屏幕上有一行黄字就停下来。

def od_json_path():
    """仓库 driver/od.json；部署时工具与 od.json 位于同一型号目录。"""
    source = os.path.normpath(os.path.join(_HERE, "..", "driver", "od.json"))
    deployed = os.path.join(_HERE, "od.json")
    return source if os.path.isfile(source) else deployed


def load_od():
    """读取并校验闸门声明；缺失、空字典与非法核对状态一律失败关闭。"""
    path = od_json_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            od = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigError("无法读取对象字典声明 {}：{}".format(path, exc))
    if not isinstance(od, dict) or str(od.get("model", "")).lower() != "pmp4010":
        raise ConfigError("对象字典缺少 PMP4010 型号声明：{}".format(path))
    entries = od.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("对象字典缺少非空 entries 数组：{}".format(path))
    keys = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigError("对象字典条目必须是对象")
        key = entry.get("key") or entry.get("name")
        if not isinstance(key, str) or not key or key in keys:
            raise ConfigError("对象字典条目名称缺失或重复：{!r}".format(key))
        keys.add(key)
        status = entry.get("status")
        if status not in ("verified", "assumed", "unverified") or "cite" not in entry:
            raise ConfigError("对象字典 {} 缺少合法 status/cite".format(key))
        cite = entry["cite"]
        if status == "verified" and (not isinstance(cite, str) or not cite.strip()):
            raise ConfigError("对象字典 {} 标为 verified 却没有 cite".format(key))
        if status == "unverified" and cite is not None:
            raise ConfigError("对象字典 {} 为 unverified，cite 必须为 null".format(key))
    return od


def od_status():
    """返回 (全部核对?, 未核对条数, 未核对条目名列表)。

    load_od 先校验非空声明、状态取值与 cite；随后只按 status 判断。
    结构型条目的 index 可以为 null，不把它误判成未核对。
    引用路径与章节的可解析性由 CI 的溯源检查负责。
    """
    od = load_od()
    bad = [e for e in od.get("entries", []) if e.get("status") != "verified"]
    names = [e.get("key") or e.get("name") or "?" for e in bad]
    return (not bad), len(bad), names


def refuse_motion(out, action):
    """运动类子命令的统一闸门。

    措辞固定（与模板一致）：`--init` 与运维脚本会 grep 「项对象字典未核对」这一行。
    """
    verified, count, names = od_status()
    if verified:
        return True
    out.bad("{} 项对象字典未核对，拒绝执行「{}」。".format(count, action))
    out.info("未核对的条目：{}".format(", ".join(names)))
    out.info("条目索引的核销走 driver/od.json 的 status/cite 两个字段；")
    out.info("全部核对并重新生成 od_generated.hpp 后，本命令才会执行。")
    return False


# ---------------------------------------------------------------------------
# 标准层的取用助手
#
# CANopen 的帧组装、SDO 收发、NMT 状态机只在 tools/hwtui/canopen.py 实现一次
# （docs/架构设计/硬件驱动开发路径.md 第 7 步）。下面这些只是**把标准层的形状
# 归一化**，不重复实现协议：
#   _std_open_active   —— 显式关闭本机回灌与自收，避免干扰探测。
#   require_std_layer  —— 发帧类子命令必须走**被审计过的那条路径**，降级实现不发。
# ---------------------------------------------------------------------------

def require_std_layer(feature):
    """发帧类子命令的准入：标准层不可用就拒绝，不悄悄走降级实现。"""
    if std_layer_available():
        return True
    sys.stderr.write(
        "标准层 tools/hwtui 不可用（{}），拒绝执行「{}」。\n"
        "  总线操作只使用共用 CANopen/SocketCAN 层，不提供内置协议或传输替代。\n"
        "  部署命令：tools/deploy_hwtools.sh\n".format(_HWTUI_ERROR, feature))
    return False


# ---------------------------------------------------------------------------
# 硬件工具发现器（tools/hwtui/discover.py）
# ---------------------------------------------------------------------------
#
# 复用而不是另写一份"本仓库有哪些硬件工具"的清单：discover.py 的约定是
# **新硬件 = 新目录**，它不持有清单，所以不存在"加了硬件忘了登记"。
# 本函数只做一件事：告诉使用者用 discover 跑本工具比直接 python3 更稳
# （discover --run 会把工具自身目录与 tools/hwtui 一起放进 sys.path，
# 这正是本文件那堆 sys.path 猜测的替代品）。

def discover_tool_path():
    """本工具在 discover.py 眼里的路径（<型号>/<文件名>）。

    用 discover.py 自己的规则算（DRIVERS_SUBDIR + 型号 + tools + 文件名），
    不在这里再拼一个相对路径的假设 —— 部署后本文件的层级与仓库里不同。
    """
    model_dir = os.path.dirname(_HERE) if os.path.basename(_HERE) == "tools" else _HERE
    return "{}/{}".format(os.path.basename(model_dir), os.path.basename(__file__))


def discover_hint():
    """使用已找到的共用发现器；无可用发现器时不提供失效建议。"""
    try:
        import discover as runner
    except ImportError:
        try:
            from hwtui import discover as runner
        except ImportError:
            return None
    return "python3 {} --run {} --".format(runner.__file__, discover_tool_path())


def _std_open_active(iface):
    """通过标准传输层打开总线，显式关闭本机回灌与本套接字自收。

    CAN_RAW_LOOPBACK 是 SocketCAN 的软件回灌开关，不配置控制器硬件回环。
    """
    bus = _canbus.CanBus(iface, loopback=False, receive_own_messages=False)
    bus.open()
    return bus


# ---------------------------------------------------------------------------
# 输出：终端时用颜色，重定向到文件时退化为纯文本。
#
# 为什么要判断 isatty：本工具的输出现场经常被人重定向到文件再贴进 issue，
# 带着 ANSI 转义码的日志没人看得下去。
# ---------------------------------------------------------------------------

class Out:
    def __init__(self, color=True):
        self.color = color and sys.stdout.isatty()

    def c(self, name, text):
        if not self.color:
            return text
        return ANSI[name] + text + ANSI["reset"]

    def plain(self, text=""):
        print(text)

    def title(self, text):
        print()
        print(self.c("bold", text))

    def ok(self, text):
        print("  " + self.c("green", "[OK]") + " " + text)

    def warn(self, text):
        print("  " + self.c("yellow", "[差异]") + " " + text)

    def bad(self, text):
        print("  " + self.c("red", "[阻塞]") + " " + text)

    def info(self, text):
        print("  " + self.c("dim", "·") + " " + text)

    def cmd(self, text):
        """逐字可复制的命令。单独一行、不带任何装饰字符 ——
        它的用途就是被复制粘贴，前后缀会一起被复制走。"""
        print()
        print("      " + text)
        print()

    def field(self, key, value):
        print("  {:<22} {}".format(key, value))


class ConfigError(Exception):
    """配置或环境层面的问题。带可执行的下一步提示，而不是一句"失败"。"""


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------

def load_device_json():
    """读设备配置。返回 (dict, 实际路径)。

    找不到就抛 ConfigError，不猜默认值：这台机器上一旦开始猜
    "can-2 大概是那个第一路吧"，物理标识就有了第二处定义。
    """
    for path in [DEVICE_JSON] + DEVICE_JSON_FALLBACKS:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return json.load(fh), path
            except (OSError, ValueError) as exc:
                raise ConfigError("{} 读取失败：{}".format(path, exc))
    raise ConfigError(
        "找不到设备配置：{}（也未找到 {}）。本工具不接受默认接口名，"
        "请先置备设备：tools/provision_device.sh".format(
            DEVICE_JSON, " / ".join(DEVICE_JSON_FALLBACKS)))


def find_can_bus(cfg, logical):
    """从唯一映射配置解析接口，拒绝缺失、重复或非法配置。"""
    if not isinstance(cfg, dict) or not isinstance(cfg.get("bus"), dict):
        raise ConfigError("设备配置缺少 bus 对象")
    matches = []
    for key in ("can", "usb_can"):
        items = cfg["bus"].get(key, [])
        if not isinstance(items, list):
            raise ConfigError("bus.{} 必须是数组".format(key))
        for item in items:
            if not isinstance(item, dict):
                raise ConfigError("bus.{} 的条目必须是对象".format(key))
            if item.get("logical") == logical:
                matches.append((item, key))
    if len(matches) != 1:
        raise ConfigError("配置里逻辑总线 {!r} 应恰有一项，实际 {} 项".format(logical, len(matches)))
    item, key = matches[0]
    iface = item.get("iface")
    if not isinstance(iface, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", iface):
        raise ConfigError("逻辑总线 {} 的 iface 缺失或非法".format(logical))
    rate = item.get("bitrate")
    if isinstance(rate, bool) or not isinstance(rate, int) or rate <= 0:
        raise ConfigError("逻辑总线 {} 缺少正整数 bitrate".format(logical))
    return item, key


# ---------------------------------------------------------------------------
# ip 命令：只读调用
#
# 全部走 `ip -details link show`，绝不 `ip link set`。理由见文件头第 1 条。
# ---------------------------------------------------------------------------

CAN_BITRATE_RE = re.compile(r"\bbitrate\s+(\d+)")
CAN_STATE_RE = re.compile(r"\bstate\s+(\w+)")


def run_readonly(argv):
    """执行一条只读命令。返回 (rc, stdout, stderr)。

    调用点全部硬编码命令列表，不接受外部拼接 —— 避免有人顺手把一条
    `ip link set` 传进来，让"这个函数只读"这句话失效。
    """
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=5)
    except FileNotFoundError:
        return 127, "", "命令不存在：{}".format(argv[0])
    except subprocess.TimeoutExpired:
        return 124, "", "命令超时：{}".format(" ".join(argv))
    return proc.returncode, proc.stdout, proc.stderr


def read_iface_actual(iface):
    """读接口现状。返回 dict；接口不存在返回 None。

    两个字段：state（UP/DOWN/...）与 bitrate（CAN 专有，只在接口被配置过
    之后才有）。读不到 bitrate 是**正常**的 —— 接口还没被 km3-can 配过。
    这就是为什么用 None 而不是 0 表示"未设置"：0 会被误读成一个位速率。
    """
    rc, out, err = run_readonly(["ip", "-details", "link", "show", iface])
    if rc != 0:
        if "does not exist" in err or "not exist" in err:
            return None
        # 其它错误（权限、ip 本身缺失）如实上报，不当成"接口不存在"。
        return {"error": err.strip() or out.strip(), "state": None, "bitrate": None,
                "raw": out}

    state = None
    m = CAN_STATE_RE.search(out)
    if m:
        state = m.group(1)
    flags = None
    lines = out.splitlines()
    head = lines[0] if lines else ""
    fm = re.search(r"<([^>]*)>", head)
    if fm:
        flags = fm.group(1)

    bitrate = None
    bm = CAN_BITRATE_RE.search(out)
    if bm:
        bitrate = int(bm.group(1))

    can_state = re.search(r"\bcan state\s+([A-Z-]+)", out)
    if flags is not None:
        state = "UP" if "UP" in flags.split(",") else "DOWN"
    return {"state": state, "can_state": can_state.group(1) if can_state else None,
            "bitrate": bitrate, "flags": flags, "raw": out, "error": None}


def parse_ip_stats(iface):
    """读取 iproute2 的 netlink 统计，RX/TX 表头后的一行才是数值。"""
    rc, out, err = run_readonly(["ip", "-s", "-details", "link", "show", iface])
    if rc != 0:
        return {"error": err.strip() or "ip 返回 {}".format(rc)}
    stats = {"raw": out}
    lines = out.splitlines()
    for pos, line in enumerate(lines[:-1]):
        kind = line.strip().split(":", 1)[0]
        if kind not in ("RX", "TX"):
            continue
        headers = line.strip().split()[1:]
        values = lines[pos + 1].split()
        for key, value in zip(headers, values):
            if value.isdigit():
                stats[kind.lower() + "_" + key] = int(value)
    return stats


def connect_bus(iface, out=None):
    """只使用共用 SocketCAN 传输层；不可用时失败，不另实现收发。"""
    if not _load_std_layer():
        raise ConfigError("标准层 tools/hwtui 不可用：{}".format(_HWTUI_ERROR))
    try:
        bus = _std_open_active(iface)
    except _canbus.CanBusError as exc:
        raise ConfigError(str(exc)) from exc
    if out is not None:
        out.info("总线实现：tools/hwtui/canbus.py（标准层）")
    return bus


def frame_can_id(f):
    """取共用层帧的仲裁 ID，兼容具有 id 字段的展示数据。"""
    if hasattr(f, "arbitration_id"):
        return f.arbitration_id
    return getattr(f, "id", 0)


def format_frame(f):
    """把标准层帧格式化成一行。"""
    data = bytes(getattr(f, "data", b""))
    hexs = " ".join("{:02X}".format(b) for b in data)
    return "{:>8X}  [{}]  {}".format(frame_can_id(f), len(data), hexs)




# ---------------------------------------------------------------------------
# 服务状态：km3-rt-controller 是否在跑
#
# 用 `systemctl is-active`（只读），不用 `systemctl status`（分页、输出量大）。
# 取不到时按"未知"处理，并按**更严格**的一侧处置 —— 见 require_rt_ack()。
# ---------------------------------------------------------------------------

def service_active(name):
    """返回 True / False / None（未知：无法判定）。

    为什么用 `systemctl show -p LoadState -p ActiveState` 而不是 `is-active`：
    `is-active` 对**尚未安装**的单元也返回 "inactive"（rc=3），
    于是"没装"和"装了但没跑"被混成一件事。这两件事的处置完全不同 ——
    没装的话根本没有另一个进程在用总线，用不着让人确认。
    实测（本机 systemctl）：
        systemctl show -p LoadState km3-rt-controller.service -> not-found
        systemctl show -p LoadState ssh.service                  -> loaded
    """
    rc, out, _ = run_readonly(["systemctl", "show", "-p", "LoadState",
                               "-p", "ActiveState", name])
    if rc != 0:
        return None
    values = {}
    for line in (out or "").splitlines():
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    load_state = values.get("LoadState")
    active_state = values.get("ActiveState")
    if load_state is None or active_state is None:
        return None
    if load_state == "not-found":
        # 单元根本没装（开发机上就是这样）。没有第二个进程在碰这条总线，
        # 因此不要求确认 —— 但这**不是**"确认过安全"，只是事实如此。
        return False
    if active_state in ("inactive", "failed"):
        return False
    if active_state in ("active", "activating", "deactivating", "reloading", "refreshing"):
        return True
    return None


def require_rt_ack(args, out, action):
    """发帧类子命令的准入检查。

    为什么需要这个开关：km3-rt-controller 在跑的时候，它（经由边界线程）
    独占这条总线的 CANopen 会话。此时从 TUI 手发一帧 NMT 或 SDO，是
    **两条控制流同时持有同一条总线** —— 轻则应答错配，重则让 4 台抬降
    电机同时"启动"。这不是工具能靠加锁避免的（锁在另一个进程里），
    只能由人确认。

    检查不到服务状态时（systemctl 不可用）**同样要求确认**：未知不等于安全。
    """
    active = service_active(RT_SERVICE)
    if active is False:
        return True
    if active is None:
        out.warn("无法确认 {} 的状态（systemctl 不可用）".format(RT_SERVICE))
    else:
        out.warn("{} 处于 active —— 它正在使用这条总线".format(RT_SERVICE))
    if args.i_know_rt_is_running:
        out.info("已给出 --i-know-rt-is-running，继续。")
        return True
    out.bad("拒绝发帧。{} 需要显式确认后才会执行。".format(action))
    out.info("确认你了解并接受上述风险后，重复命令并加上：--i-know-rt-is-running")
    return False


# ---------------------------------------------------------------------------
# can.env 写入
# ---------------------------------------------------------------------------

def read_can_env():
    """读 /etc/km3/can.env。返回 (值字典, 原始行, 是否存在)。"""
    if not os.path.exists(CAN_ENV):
        return {}, [], False
    try:
        with open(CAN_ENV, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        raise ConfigError("{} 读取失败：{}".format(CAN_ENV, exc))
    values = {}
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        values[k.strip()] = v.strip()
    return values, lines, True


def write_can_env_bitrate(bitrate):
    """把 CAN_BITRATE= 写进 can.env，保留其余行与注释。

    为什么要保留其余内容：这个文件同时承载 CAN_IFACES 等现场参数
    （km3-can.service 通过 EnvironmentFile 读它）。整文件重写会把
    别人设过的接口列表悄悄清掉。

    重复的 CAN_BITRATE= 行会被丢弃而不是留着：systemd 是**后者覆盖前者**，
    留着重复行会让人误判生效值 —— 顺手消掉一个坑。
    """
    if isinstance(bitrate, bool) or not isinstance(bitrate, int) or bitrate <= 0:
        raise ConfigError("CAN_BITRATE 必须为正整数")
    values, lines, existed = read_can_env()
    target = "CAN_BITRATE={}".format(bitrate)

    replaced = False
    out_lines = []
    for line in lines:
        s = line.strip()
        if s.partition("=")[0].strip() == "CAN_BITRATE":
            if not replaced:
                out_lines.append(target)
                replaced = True
            continue
        out_lines.append(line)
    if not replaced:
        out_lines.append(target)

    text = "\n".join(out_lines) + "\n"
    try:
        fd = os.open(CAN_ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except PermissionError:
        raise ConfigError(
            "{} 不可写。该文件为 km3:km3 0640；请以 km3 身份运行本工具"
            "（或先由置备脚本调整权限）。".format(CAN_ENV))
    except OSError as exc:
        raise ConfigError("{} 写入失败：{}".format(CAN_ENV, exc))
    current, _, _ = read_can_env()
    if current.get("CAN_BITRATE") != str(bitrate):
        raise ConfigError("{} 写入后读回值不一致".format(CAN_ENV))
    return existed, values.get("CAN_BITRATE")


# ---------------------------------------------------------------------------
# 面板数据（纯函数：无终端、无 curses、无 I/O 副作用）
# ---------------------------------------------------------------------------
#
# 面板与纯文本共用同一份数据：终端画不了时 panel_lines() 把它们原样打出来。
# 这样「面板上看到的」与「重定向到文件里读到的」不可能对不上 ——
# 同一事实两处排版，是这类工具最常见的漂移。
#
# 尺寸不在这里处理：板端 `stty size` 返回 0 0（实测），一律交给
# tools/hwtui/tui.py 的 resolve_size()（ioctl -> LINES/COLUMNS -> 24x80），
# 本文件不去问终端大小。

def panel_rows(iface, cfg_path, actual, declared, rt_state):
    """面板/纯文本共用的字段表。每行 (key_en, label_zh, value, source)。

    字段名给两份，不是冗余：tools/hwtui/tui.py 的字形边界**刻意只到 ASCII
    与单宽制表符号**（见那个文件的文件头），所以 curses 表格里排不了中文 ——
    它要用 key_en；纯文本输出没有这个限制，用 label_zh 更贴现场。
    source 一列是"这个值从哪来"，两种输出都用它，避免有人对着面板猜数字来源。
    """
    if actual is None:
        link, rate = "missing", "n/a"
    elif actual.get("error"):
        link, rate = "read failed", "n/a"
    else:
        link = actual.get("state") or "unknown"
        cur = actual.get("bitrate")
        rate = "{} bps".format(cur) if cur is not None else "unset"

    rt_text = {True: "active (using bus)", False: "inactive",
               None: "unknown (confirmation needed)"}[rt_state]

    try:
        env_values, _, env_exists = read_can_env()
    except ConfigError:
        env_bitrate = "unreadable"
    else:
        env_bitrate = env_values.get("CAN_BITRATE", "unset") if env_exists else "missing file"

    try:
        verified, count, names = od_status()
    except ConfigError:
        od_text = "od.json unavailable/invalid"
    else:
        od_text = "verified" if verified else "{} unverified -> motion blocked".format(count)

    return [
        ("logical bus", "逻辑总线", LOGICAL_BUS,
         "device.json bus.can[].logical"),
        ("iface", "内核接口", iface or "(未配置)",
         "device.json bus.can[].iface"),
        ("CAN state", "CAN 状态",
         (actual or {}).get("can_state") or "unknown", "ip -details link show"),
        ("link state", "链路状态", link, "ip -details link show"),
        ("link bitrate", "链路位速率", rate, "ip -details link show"),
        ("declared rate", "配置声明速率",
         "{} bps".format(declared) if declared else "missing",
         "device.json bus.can[].bitrate"),
        ("can.env rate", "can.env 速率", env_bitrate, CAN_ENV),
        ("rt-controller", "实时控制器", rt_text, "systemctl show -p ActiveState"),
        ("od gate", "对象字典", od_text, "driver/od.json"),
        ("config file", "配置文件", cfg_path, "loaded path"),
    ]


def _display_width(text):
    """终端显示宽度：东亚双宽字符算 2 列，其余算 1 列。

    用 unicodedata.east_asian_width 做判定，返回 W（宽）或 F（全角）时算双宽；
    Na / N / H / A 算单宽。比手写 Unicode 范围表更准，也不会触发
    check-manual-citations.sh 的「源码索引字面量」检查（那把 0x1100 这类
    16 进制值当成 CANopen 对象字典索引要求引用注释）。
    """
    import unicodedata as _ud  # noqa: PLC0415  — 延迟 import 保持顶层最小
    width = 0
    for ch in text:
        eaw = _ud.east_asian_width(ch)
        width += 2 if eaw in ('W', 'F') else 1
    return width


def _pad(text, width):
    """按显示宽度右补空格到 width 列。"""
    return text + " " * max(0, width - _display_width(text))


def panel_lines(rows, header):
    """把字段表排成纯文本。宽度按最长字段名的**显示宽度**定，不依赖终端宽度。"""
    width = max([_display_width(label) for _, label, _, _ in rows] + [0])
    lines = [header, ""]
    for _, label, value, source in rows:
        lines.append("  {}  {}".format(_pad(label, width), value))
        lines.append("  {}  └ {}".format(" " * width, source))
    return lines


class HwPanel(object):
    """curses 面板，复用 tools/hwtui/tui.py 的 TuiApp 外壳。

    两条退化路径，都不报错也不画半屏：
      - hwtui 不可用 / stdout 不是 tty / TERM=dumb -> show() 直接打印纯文本；
      - 进得了 curses -> 画一屏，按 q/Esc 退出后 TuiApp 自己恢复终端。

    **面板只读**：它不开总线、不发帧、不改任何文件。数据由调用方在进 curses
    之前读好（curses 里做 I/O 会把一屏画成挤牙膏，且读失败没法降级成文本）。

    rows/cols 只在本文件无法自己拿到终端尺寸时才有意义 —— 板端实测
    `stty size` 返回 0 0，因此这两个参数是给那种会话兜底用的；
    解析顺序（ioctl -> LINES/COLUMNS -> 命令行 -> 24x80）由 tui.resolve_size() 决定。
    """

    def __init__(self, rows, enable=True, rows_forced=None, cols_forced=None,
                 box=None):
        # rows 收「列表」或「无参可调用」两种：调用方在进 curses 前读好的通常
        # 是一次性的列表，但留一个可调用入口给将来的定时刷新（poll() 里重读）。
        if callable(rows):
            self._provider = rows
        else:
            _fixed = list(rows)
            self._provider = lambda: _fixed
        self.enabled = bool(enable)
        self._rows = rows_forced
        self._cols = cols_forced
        self._box = box
        self.app = None

    def _make_app(self):
        try:
            from tui import Table, TuiApp     # 扁平：板上 hwtui/ 在 sys.path 上
        except ImportError:
            from hwtui.tui import Table, TuiApp
        provider = self._provider

        class _App(TuiApp):
            title = "PMP4010 lift motor A  (read-only panel)"

            def __init__(self):
                TuiApp.__init__(self, rows=rows_forced, cols=cols_forced, box=box_style)
                self._table = Table(headers=("field", "value", "source"),
                                    rows=[], gap="  ")

            def hints(self):
                return ["q quit", "snapshot: no frames sent"]

            def build(self):
                grid, lay = self.shell_grid()
                # 只喂 ASCII 字段名：tui.py 不处理东亚字宽，中文字段名在 curses 里
                # 会比它声称的窄一半，整列错位（值列被推左）。
                self._table.set_rows(
                    [(key_en, value, source)
                     for key_en, _label, value, source in provider()])
                if lay.content.valid:
                    self._table.render(grid, lay.content, self.box)
                return grid

        rows_forced, cols_forced, box_style = self._rows, self._cols, self._box
        return _App

    def _enter(self):
        if not self.enabled:
            return self
        try:
            cls = self._make_app()
        except ImportError:
            self.enabled = False
            return self
        app = cls()
        # _curses_usable() 是 TuiApp 的只读判据（tty + TERM）。不成立时直接
        # 退回纯文本，不用 TuiApp.run() 的"往 stderr 打一帧"那条路。
        if not app._curses_usable():
            self.enabled = False
            return self
        self.app = app
        return self

    def __enter__(self):
        return self._enter()

    def __exit__(self, exc_type, exc, tb):
        return False

    def show(self, fallback_lines):
        """画一屏；画不了就打印 fallback_lines。返回是否真的用了 curses。"""
        self._enter()
        if not self.enabled or self.app is None:
            for line in fallback_lines:
                print(line)
            return False
        try:
            self.app.run()
        except KeyboardInterrupt:
            pass
        return True


def status_lines(iface, cfg_path, actual, declared_bitrate, rt_state=None):
    """概览行（纯函数）。等价于 panel_lines(panel_rows(...))，保留这个名字是因为
    --init 的调用点与 README 都按它来。"""
    rows = panel_rows(iface, cfg_path, actual, declared_bitrate,
                      service_active(RT_SERVICE) if rt_state is None else rt_state)
    return panel_lines(rows, "PMP4010 机身抬降电机 A  —  逻辑名 {}".format(LOGICAL_BUS))


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def parse_node_range(text):
    """解析 "1-4" / "1"。非法输入直接报错，不静默取默认。"""
    text = text.strip()
    if "-" in text:
        a, _, b = text.partition("-")
        lo, hi = int(a), int(b)
    else:
        lo = hi = int(text)
    if not (1 <= lo <= 127 and 1 <= hi <= 127 and lo <= hi):
        raise ValueError("节点号范围必须在 1..127 且 lo <= hi，收到 {!r}".format(text))
    return lo, hi


def cmd_init(args, out):
    """--init：三段式。顺序不可颠倒，理由见文件头。"""
    out.title("1/3 读配置与现状（只读）")
    cfg, cfg_path = load_device_json()
    item, array_name = find_can_bus(cfg, LOGICAL_BUS)
    iface = item.get("iface", "")
    bitrate = item.get("bitrate")

    out.field("配置文件", cfg_path)
    out.field("配置数组", "bus.{}".format(array_name))
    out.field("逻辑名", LOGICAL_BUS)
    out.field("物理接口", iface if iface else out.c("red", "(空 — 配置未填)"))
    out.field("声明位速率",
              "{} bps".format(bitrate) if bitrate else out.c("red", "(缺失)"))

    if not iface:
        out.bad("device.json 里 can-2 的 iface 为空 —— 设备尚未置备。")
        out.info("先运行 tools/provision_device.sh，再回来跑本命令。")
        out.info("这里**不猜**一个接口名出来：物理标识只能有配置这一处来源。")
        return 2

    actual = read_iface_actual(iface)
    if actual is None:
        out.warn("接口 {} 不存在 —— {} 尚未把它拉起（或 SPI-CAN 驱动未加载）。".format(
            iface, CAN_SERVICE))
    elif actual.get("error"):
        out.warn("读取 {} 现状失败：{}".format(iface, actual["error"]))
    else:
        out.field("接口 state", actual.get("state"))
        cur = actual.get("bitrate")
        out.field("接口 bitrate", "{} bps".format(cur) if cur is not None
                  else out.c("yellow", "(未设置)"))

        # 差异逐条报，不合并成一句"不一致"：现场需要知道**哪一项**不一致。
        diffs = []
        if bitrate and cur is not None and cur != bitrate:
            diffs.append("位速率：配置 {} != 现状 {}".format(bitrate, cur))
        elif bitrate and cur is None:
            diffs.append("位速率：配置 {}，接口尚未被配置".format(bitrate))
        if actual.get("state") and actual["state"] != "UP":
            diffs.append("链路状态：{}（期望 UP）".format(actual["state"]))
        if diffs:
            for d in diffs:
                out.warn(d)
        else:
            out.ok("现状与配置一致")

    # 现状还有第二处：运行时生效的 CAN_BITRATE= 在 can.env 里，不在接口上
    # （接口被 down 之后 `ip -details` 就不再显示 bitrate 了）。两处都要读，
    # 否则现场会出现"接口看不出来、但 km3-can 下次启动用的是另一个值"。
    try:
        env_values, _, env_exists = read_can_env()
    except ConfigError as exc:
        out.warn(str(exc))
        env_values, env_exists = {}, False
    env_bitrate = env_values.get("CAN_BITRATE")
    out.field("{} 中的 CAN_BITRATE".format(CAN_ENV),
              env_bitrate if env_bitrate is not None
              else out.c("yellow", "(未设置)"))
    if bitrate and env_bitrate is not None and str(bitrate) != str(env_bitrate):
        out.warn("位速率：配置声明 {} != {} 中的 {}（写入模式会改成声明值；--verify 不写入）".format(
            bitrate, CAN_ENV, env_bitrate))

    if not args.no_tui:
        # 面板：走 tools/hwtui/tui.py 的外壳（HwPanel）。画不了终端就退化成
        # 同一份数据的纯文本 —— 两处排版同一个数据源，所以面板上的数字
        # 与重定向到文件里的数字不可能对不上。
        rows = panel_rows(iface, cfg_path, actual, bitrate, service_active(RT_SERVICE))
        HwPanel(rows, enable=True, rows_forced=args.rows, cols_forced=args.cols,
                box="ascii" if args.ascii else None).show(
            panel_lines(rows, "PMP4010 接入现状（只读）"))

    if args.verify:
        out.title("2/3 跳过写入（--verify 是纯只读）")
        out.info("--verify 下不修改任何文件，任何身份都可运行。")
    else:
        out.title("2/3 写目标位速率到 {}".format(CAN_ENV))
        if not bitrate:
            out.bad("配置里没有位速率，无法写入。")
            return 2
        try:
            existed, old = write_can_env_bitrate(bitrate)
        except ConfigError as exc:
            out.bad(str(exc))
            return 3
        out.ok("{} CAN_BITRATE={}（原值 {}）".format(
            "已更新并读回" if existed else "已创建并读回", bitrate,
            old if old is not None else "无"))
        out.info("该文件由 km3 拥有（0640 km3:km3），本次写入无需提权。")
        out.info("文件内其余行（CAN_IFACES 等现场参数）未被改动。")

    out.title("3/3 需要 root 执行的一步（本工具不代劳）")
    out.info("接口状态归 {} 所有，本工具不接管。".format(CAN_SERVICE))
    out.info("km3 用户 CapEff=0：`ip link set` 报 Operation not permitted，")
    out.info("`systemctl restart` 报 Interactive authentication required。")
    out.info("仅在现场授权并需要应用新配置时，由人执行下面的命令：")
    out.cmd("sudo systemctl restart {}".format(CAN_SERVICE))
    out.info("执行后可用 `python3 {} --bus-stats` 复查现状。".format(
        os.path.basename(sys.argv[0])))
    return 0


def cmd_scan(args, out):
    """--scan：SDO 扫节点（只读探测，但**会在总线上发帧**）。

    「只读」在这里指的是**不发任何写请求**：探节点只发 SDO 上传请求，
    这是 docs/架构设计/硬件驱动开发路径.md 第 12 步明确允许的
    （"只读探测本身会发送 SDO 上传请求，这是允许的；不允许的是任何写"）。
    但它终究是帧，所以 km3-rt-controller active 时同样要人确认。

    **用 SDO 读 0x1000 探，不用心跳探**：心跳可能被 0x1017 关掉（周期 0），
    也可能周期长于扫描窗口；而一个在 Pre-operational 状态的节点照样回 SDO。
    这条判据来自标准层 canopen.scan_nodes()，本文件不另写一份。

    为什么身份四项（0x1000/0x1008/0x100A/0x1018）属于**规范公共对象**，
    与 driver/od.json 的核对状态无关：它们是标准通信对象，
    定义在 tools/hwtui/canopen.py 里；具体设备是否实现逐项报告。因此本子命令**不受
    OD 闸门限制** —— 读不出厂家对象不等于不能问"你是什么型号、哪版固件"。
    """
    out.info("本子命令会发出 SDO 上传请求（读 0x1000/0x1008/0x100A/0x1018）。")
    out.info("零写请求、零 NMT、零 PDO —— 不会让任何电机进入使能或运动。")
    if not require_rt_ack(args, out, "SDO 扫节点"):
        return 4
    if not require_std_layer("--scan"):
        return 5

    cfg, _ = load_device_json()
    item, _ = find_can_bus(cfg, LOGICAL_BUS)
    iface = item.get("iface", "")
    out.title("SDO 扫节点 {}（{}）".format(LOGICAL_BUS, iface or "未配置"))
    if not iface:
        out.bad("配置里 {} 的 iface 为空，无处可扫。".format(LOGICAL_BUS))
        return 2

    if args.node_range:
        lo, hi = parse_node_range(args.node_range)
        nodes = list(range(lo, hi + 1))
        out.field("扫描范围", "{}-{}".format(lo, hi))
    else:
        # 默认 1..127：手册给了 1~127 这个范围（E06:3.6.2），但**没说这 4 台
        # 分别是几号**。因此默认全扫，不假装知道范围。
        nodes = list(range(1, 128))
        out.info("未给 --node-range，扫 1..127。现场各台设备的节点号尚未确认 ——")
        out.info("手册只给了取值范围 1~127（E06:3.6.2），没给这 4 台各自的编号。")

    out.field("节点数 × 超时", "{} × {}s ≈ {:.1f}s".format(
        len(nodes), args.timeout, len(nodes) * args.timeout))

    try:
        bus = _std_open_active(iface)
    except ConfigError as exc:
        out.bad(str(exc))
        return 3
    except Exception as exc:                                # noqa: BLE001
        out.bad("打开 {} 失败：{}".format(iface, exc))
        return 3

    try:
        found = _canopen.scan_nodes(bus, nodes=nodes, timeout=args.timeout)
        print()
        if not found:
            out.warn("{} 个节点无一应答。".format(len(nodes)))
            out.info("无应答不能区分无从站、位速率、接线或通信模式不符；请检查 --bus-stats。")
            return 1
        out.field("应答节点", "{} 个：{}".format(len(found), ", ".join(str(n) for n in found)))
        if args.identity:
            for node_id in found:
                client = _canopen.SdoClient(bus, node_id, timeout=args.timeout)
                _print_identity(out, client, node_id)
        else:
            out.info("加 --identity 可额外读取设备类型、名称、软件版本与身份子对象。")
        return 0
    finally:
        bus.close()


def _print_identity(out, client, node_id):
    """读一个节点的身份四项。逐项 try：一项读不到不影响其余项。

    刻意**不**用 read_identity() 那个一次性聚合函数：它任一项抛异常就整体失败，
    而现场需要的恰恰是"哪几项读到了、哪项没读到"。逐项读才有这个分辨率。
    """
    print("  节点 {}:".format(node_id))
    for label, fn in (
        ("0x1000 设备类型", lambda: "0x{:08X}".format(client.read_device_type())),
        ("0x1008 设备名称", client.read_manufacturer_name),
        ("0x100A 软件版本", client.read_software_version),
        ("0x1018 身份对象", lambda: _identity_text(client)),
    ):
        try:
            print("    {:<16} {}".format(label, fn()))
        except _canopen.CanopenError as exc:
            print("    {:<16} 读取失败：{}".format(label, exc))
            out.info("    （可选对象缺失或从站拒绝，**不代表节点不在**）")


def _identity_text(client):
    identity = client.read_identity()
    return " ".join("{}={}".format(key, "0x{:08X}".format(value)
                                  if key != "count" else value)
                    for key, value in identity.items())


def cmd_nmt(args, out):
    """--nmt：发 NMT 命令（广播单帧）。

    **NMT 属于运动类**（Start 会让从站进入可运动状态），因此走 OD 闸门：
    手册已确认 0x6040/0x6041 的含义，但同步周期、上电状态等条目仍未核对，
    在"上电后从站会怎样"确定之前，让 4 台抬降电机同时 Start 不是这个工具该做的事。
    闸门在**最前**：OD 未核对时必须先看到「N 项对象字典未核对」，
    而不是一条"取值非法"的语法错误 —— 后者会把人的注意力引到错误的方向。
    """
    if not refuse_motion(out, "nmt"):
        return 3
    if not require_std_layer("--nmt"):
        return 5
    if not require_rt_ack(args, out, "发送 NMT"):
        return 4

    cfg, _ = load_device_json()
    item, _ = find_can_bus(cfg, LOGICAL_BUS)
    iface = item.get("iface", "")
    out.title("NMT {} -> {}".format(args.nmt_command, args.node if args.node else "广播"))
    if not iface:
        out.bad("配置里 {} 的 iface 为空。".format(LOGICAL_BUS))
        return 2

    # 命令名 -> 命令码**从标准层取**，不在本文件抄一份：抄一份就多了一处会漂移的真相。
    commands = dict(getattr(_canopen, "NMT_COMMANDS", {}))
    if args.nmt_command in commands:
        code = commands[args.nmt_command]
    else:
        # 别名：模板与文档里用的是 start/stop/preop 这类短名，标准层用
        # start_remote_node/stop_remote_node/enter_pre_operational 这种规范名。
        # 只在这里做一次映射，且**必须**映射到标准层确实存在的键。
        alias = {"start": "start_remote_node", "stop": "stop_remote_node",
                 "preop": "enter_pre_operational", "reset-node": "reset_node",
                 "reset-comm": "reset_communication"}
        key = alias.get(args.nmt_command)
        code = commands.get(key) if key else None
    if code is None:
        out.bad("未知 NMT 命令 {!r}；可用：{}".format(
            args.nmt_command, ", ".join(sorted(commands))))
        return 2
    node = args.node if args.node else 0
    if not 0 <= node <= 127:
        out.bad("节点号必须在 0..127（0 = 广播）")
        return 2

    out.info("NMT 是广播命令：一次影响总线上**所有**匹配的从站。")
    out.info("节点号 {} 表示 {}".format(node, "全体（广播）" if node == 0 else "仅该节点"))
    try:
        bus = _std_open_active(iface)
    except Exception as exc:                                # noqa: BLE001
        out.bad("打开 {} 失败：{}".format(iface, exc))
        return 3
    try:
        master = _canopen.NmtMaster(bus)
        frame = master.send_command(code, node)
        out.ok("已发送（标准层 NmtMaster）：COB-ID=0x{:03X} data={}".format(
            frame_can_id(frame), bytes(frame.data).hex(" ").upper()))
    finally:
        bus.close()
    return 0


class MotionError(RuntimeError):
    """运动事务失败；命令返回非零，不能把 SDO 确认当作状态到达。"""


class MotionSession:
    """非实时、单节点 CiA402 操作，所有 SDO 均经共用协议层。

    超时是主机策略，非厂家参数。每次事务都限制在当前绝对期限内，
    状态轮询不会因持续收到未到达状态而无限延长。
    """

    def __init__(self, client, out, state_timeout):
        try:
            import canopen_consts as od
        except ImportError:
            try:
                from hwtui import canopen_consts as od
            except ImportError as exc:
                raise ConfigError("缺少同版生成常量 canopen_consts.py：{}".format(exc)) from exc
        self.od = od
        self.client = client
        self.out = out
        self.state_timeout = state_timeout
        self.sdo_timeout = client.timeout
        self.needs_stop = False

    def deadline(self):
        return time.monotonic() + self.state_timeout

    def call(self, deadline, label, method, *args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MotionError("{}：超时".format(label))
        self.client.timeout = min(self.sdo_timeout, remaining)
        try:
            result = method(*args)
        except (_canopen.CanopenError, _canbus.CanBusError) as exc:
            raise MotionError("{}：{}".format(label, exc)) from exc
        finally:
            self.client.timeout = self.sdo_timeout
        if time.monotonic() >= deadline:
            raise MotionError("{}：超时".format(label))
        return result

    def status(self, deadline):
        return self.call(deadline, "读取状态字", self.client.read_uint16,
                         self.od.STATUSWORD, 0)

    def control(self, value, deadline):
        # 每次控制字下载都重新核对，失败时连失能/快停也不能假定可用。
        # 与上传共用的 call() 不加此守卫，避免递归或给所有只读事务加读请求。
        self.require_cia402(deadline)
        # 写确认丢失也可能已被电机执行，因此在发送前标记清理责任。
        if value in (_canopen.CIA402_SHUTDOWN, _canopen.CIA402_SWITCH_ON,
                     _canopen.CIA402_ENABLE, _canopen.CIA402_START_HOMING):
            self.needs_stop = True
        self.call(deadline, "写控制字 0x{:04X}".format(value),
                  self.client.write_uint16, self.od.CONTROLWORD, 0, value)

    def pause(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.02, remaining))

    def wait_status(self, predicate, label, deadline, allow_fault=False):
        last = None
        while time.monotonic() < deadline:
            last = self.status(deadline)
            if not allow_fault and last & _canopen.CIA402_FAULT:
                raise MotionError("{}：故障状态字 0x{:04X}".format(label, last))
            if predicate(last):
                return last
            self.pause(deadline)
        raise MotionError("{}：超时，最后状态字 {}".format(
            label, "未知" if last is None else "0x{:04X}".format(last)))

    def require_cia402(self, deadline=None):
        mode = self.call(self.deadline() if deadline is None else deadline,
                         "读取控制模式 2002h:01h",
                         self.client.read_uint16, self.od.CONTROL_MODE_SELECT, 1)
        if mode != 0:
            raise MotionError("2002h:01h={}，不是 CiA402 模式；请先完成控制模式配置".format(mode))

    def enable(self):
        self.require_cia402()
        mode = self.call(self.deadline(), "读取运行模式", self.client.read_uint8,
                         self.od.MODES_OF_OPERATION_DISPLAY, 0)
        # E06 enable_sequence：VM(2)需0x7F，IP(7)需0x1F；本命令只实现0x0F。
        # 未实现的模式专用启动/PDO编排不能由通用使能顺手触发。
        if mode not in (1, 3, 4, 6, 8, 9, 10):
            raise MotionError("运行模式 {} 不支持本命令的 0x0F 使能序列".format(mode))
        sw = self.status(self.deadline())
        if sw & _canopen.CIA402_FAULT:
            # Fault reaction active 必须先结束，复位是脱使能后的 bit7 上升沿。
            self.wait_status(lambda value: value & _canopen.CIA402_DISABLED_MASK == _canopen.CIA402_FAULT,
                             "等待 Fault", self.deadline(), allow_fault=True)
            deadline = self.deadline()
            self.control(_canopen.CIA402_DISABLE, deadline)
            self.control(_canopen.CIA402_FAULT_RESET, deadline)
            self.wait_status(lambda value: not value & _canopen.CIA402_FAULT,
                             "故障复位", deadline, allow_fault=True)
            self.control(_canopen.CIA402_DISABLE, deadline)
        elif sw & _canopen.CIA402_MASK == _canopen.CIA402_ENABLED:
            return
        elif sw & _canopen.CIA402_DISABLED_MASK == 0:
            self.wait_status(lambda value: value & _canopen.CIA402_DISABLED_MASK == _canopen.CIA402_DISABLED,
                             "等待 Switch on disabled", self.deadline())
        for word, state, label in ((_canopen.CIA402_SHUTDOWN, _canopen.CIA402_READY, "Ready to switch on"),
                                   (_canopen.CIA402_SWITCH_ON, _canopen.CIA402_SWITCHED_ON, "Switched on"),
                                   (_canopen.CIA402_ENABLE, _canopen.CIA402_ENABLED, "Operation enabled")):
            deadline = self.deadline()
            self.control(word, deadline)
            self.wait_status(lambda value: value & _canopen.CIA402_MASK == state, label, deadline)

    def disable(self):
        deadline = self.deadline()
        self.control(_canopen.CIA402_DISABLE, deadline)
        self.wait_status(lambda value: value & _canopen.CIA402_DISABLED_MASK == _canopen.CIA402_DISABLED,
                         "Switch on disabled", deadline)

    def stop(self):
        deadline = self.deadline()
        self.control(_canopen.CIA402_QUICK_STOP_COMMAND, deadline)
        self.wait_status(lambda value: value & _canopen.CIA402_MASK == _canopen.CIA402_QUICK_STOP or
                         value & _canopen.CIA402_DISABLED_MASK == _canopen.CIA402_DISABLED,
                         "Quick stop active / Switch on disabled", deadline)

    def home(self, method, timeout):
        # E06 §6.7.5 要求切换模式前失能，不能照任务示例先使能再改模式。
        self.require_cia402()
        self.needs_stop = True
        self.disable()
        deadline = self.deadline()
        self.call(deadline, "设置 Homing mode", self.client.write_uint8,
                  self.od.MODES_OF_OPERATION, 0, 6)
        while True:
            mode = self.call(deadline, "确认 Homing mode", self.client.read_uint8,
                             self.od.MODES_OF_OPERATION_DISPLAY, 0)
            if mode == 6:
                break
            self.pause(deadline)
        self.call(deadline, "设置回零方法", self.client.write_uint8,
                  self.od.HOMING_METHOD, 0, method)
        readback = self.call(deadline, "回读回零方法", self.client.read_uint8,
                             self.od.HOMING_METHOD, 0)
        if readback != method:
            raise MotionError("回零方法回读不一致：期望 {}，收到 {}".format(method, readback))
        self.enable()
        deadline = time.monotonic() + timeout
        # 明确产生 bit4 的 0 -> 1 边沿，重复回零也不能依赖上次控制字。
        self.control(_canopen.CIA402_ENABLE, deadline)
        baseline = self.status(deadline)
        # E06 表6-19 分列 bit10/bit12；主机额外要求两者同时成立，
        # 避免只到达目标或只完成寻原点就提前报告整次回零完成。
        homing_complete = (_canopen.CIA402_HOMING_TARGET_REACHED |
                           _canopen.CIA402_HOMING_ATTAINED)
        seen_incomplete = baseline & homing_complete != homing_complete

        def completed(sw):
            nonlocal seen_incomplete
            if sw & _canopen.CIA402_HOMING_ERROR:
                raise MotionError("回零错误（bit13），状态字 0x{:04X}".format(sw))
            if sw & _canopen.CIA402_MASK != _canopen.CIA402_ENABLED:
                raise MotionError("回零期间退出 Operation enabled：0x{:04X}".format(sw))
            if sw & homing_complete != homing_complete:
                seen_incomplete = True
            # 上次回零的完成位可能尚未清除，不能把旧完成当作本次完成。
            return seen_incomplete and sw & homing_complete == homing_complete

        completed(baseline)  # 启动前也必须健康且仍处于使能状态。
        self.control(_canopen.CIA402_START_HOMING, deadline)
        self.out.info("回零中：方法 {}，最多等待 {} 秒".format(method, timeout))
        self.wait_status(completed, "等待回零完成", deadline)
        self.control(_canopen.CIA402_ENABLE, deadline)

    def stop_after_failure(self):
        if not self.needs_stop:
            return
        self.out.info("操作未完成，尝试 Quick stop。")
        try:
            self.stop()
        except (MotionError, KeyboardInterrupt) as exc:
            self.out.bad("Quick stop 未确认：{}；需由现场独立停机措施处置。".format(exc))
        else:
            self.out.info("已确认 Quick stop active / Switch on disabled 状态。")


def cmd_motion(args, out):
    """三条准入闸门全部通过后，才打开单节点 SDO 会话。"""
    out.title("CiA402 {} 节点 {}".format(args.motion, args.node))
    if not refuse_motion(out, "motion"):
        return 3
    if not require_std_layer("--motion"):
        return 5
    if not require_rt_ack(args, out, "CiA402 " + args.motion):
        return 4
    if args.node is None or not 1 <= args.node <= 127:
        out.bad("--motion 必须显式指定 --node 1..127，不支持广播。")
        return 2
    cfg, _ = load_device_json()
    item, _ = find_can_bus(cfg, LOGICAL_BUS)
    bus = connect_bus(item["iface"], out)
    session = None
    try:
        client = _canopen.SdoClient(bus, args.node, timeout=args.timeout)
        session = MotionSession(client, out, args.state_timeout)
        if args.motion == "home":
            out.info("回零使用已配置的限位/原点输入、速度与加速度；默认方法17=负限位开关。")
            session.home(args.homing_method, args.home_timeout)
            out.ok("回零完成（bit10、bit12 已确认）")
        else:
            getattr(session, args.motion)()
            messages = {"enable": "电机已使能（Operation enabled）",
                        "disable": "电机已禁用（Switch on disabled）",
                        "stop": "已确认 Quick stop active / Switch on disabled 状态"}
            out.ok(messages[args.motion])
        return 0
    except (MotionError, KeyboardInterrupt):
        if session is not None:
            session.stop_after_failure()
        raise
    finally:
        bus.close()


def cmd_od_dump(args, out):
    """--od-dump：打印对象字典声明与核对状态（只读）。

    一条目一行的 key/status/index/sub/cite 就是**闸门的原始依据**：
    现场看到"N 项未核对"时，第一件事就是来这里看是哪 N 项、缺的是哪份资料。
    因此表格不隐藏未核对条目，也不给它们填默认值 ——
    null 就是 null，它表达的是"手册没写"。
    """
    out.title("对象字典声明（driver/od.json）")
    path = od_json_path()
    try:
        od = load_od()
    except ConfigError as exc:
        out.bad(str(exc))
        out.info("本子命令读的是仓库内的声明文件；板端只部署工具时该文件可能不在。")
        out.info("驱动源码里同一个文件在 device/drivers/pmp4010/driver/od.json。")
        return 2

    out.info("只读子命令：不发帧，不要求 --i-know-rt-is-running。")
    try:
        verified, count, names = od_status()
    except ConfigError as exc:
        out.bad(str(exc))
        return 2
    if verified:
        out.ok("全部 {} 条 status=verified —— 运动类子命令的闸门已开。".format(
            len(od.get("entries", []))))
    else:
        out.bad("{} 项对象字典未核对（共 {} 条）—— 运动类子命令被拒绝。".format(
            count, len(od.get("entries", []))))
        out.info("未核对：{}".format(", ".join(names)))
        out.info("一行 cite 对应一处手册出处，消解方式见 docs/硬件手册/PMP4010/README.md。")
    print()

    header = "{:<34} {:<11} {:<9} {:<5} {}".format("key", "status", "index", "sub", "cite")
    print("  " + out.c("bold", header))
    print("  " + out.c("dim", "-" * min(100, max(len(header), 40))))
    for e in od.get("entries", []):
        cite = e.get("cite")
        index = e.get("index")
        print("  {:<34} {:<11} {:<9} {:<5} {}".format(
            e.get("key") or e.get("name") or "?",
            e.get("status", "?"),
            "null" if index is None else str(index),
            "null" if e.get("sub") is None else str(e["sub"]),
            "null" if cite is None else cite))
    print()
    out.field("条目总数", len(od.get("entries", [])))
    out.field("未核对条目", count)
    out.field("已核对条目", len(od.get("entries", [])) - count)

    device = od.get("device", {})
    print()
    for key in ("model", "role", "units_on_bus", "logical_bus", "protocol",
                "physical_layer"):
        if key in device:
            out.field(key, device[key])
    # 设备块的子条目：逐个报 status，并且**把出处打在旁边** ——
    # 这些块里的 node_id / termination 是现场最常问的两条。
    print()
    for key, entry in sorted(device.items()):
        if not isinstance(entry, dict) or "status" not in entry:
            continue
        out.field(key, "{}  cite={}".format(
            entry.get("status"), entry.get("cite") or "—"))

    if args.dump_json:
        print()
        print(json.dumps(od, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CAN 专用错误计数（netlink IFLA_INFO_XSTATS 的文本渲染）
# ---------------------------------------------------------------------------
#
# **不存在于 sysfs。** /sys/class/net/<if>/statistics/ 里只有通用的
# rx_errors / tx_errors，对 CAN 恒为 0；拿它们冒充会让总线健康的假象成立，
# 而缺终端电阻的症状恰恰是"错误计数在涨、应用层毫无报错"。
#
# iproute2 的 `ip -details -statistics link show <if>` 会把 xstats 渲染成
# 一行连排的 `key value key value ...`（不是每行一个），键名形如
# bus-error / bus-errors / error-warn / error-pass / bus-off / restart-ms /
# arbit-lost / re-started。因此这里按词扫描，遇到已知键就取下一个词当值。
#
# **列表只收已知键**：未知键不猜含义（iproute2 版本不同会加键），
# 但也照原样留下来给 --show-raw 看，不静默丢。

CAN_XSTAT_KEYS = (
    "restart-ms", "bus-error", "bus-errors", "error-warn", "error-pass",
    "bus-off", "arbit-lost", "arbitration-lost", "re-started", "receive-error-counter",
    "transmit-error-counter",
)

#: 第 12 步完成判据点名要打印的三个。缺任何一个都要明说缺了哪一个。
CAN_XSTAT_REQUIRED = ("bus-off", "error-warn", "arbitration-lost")


def parse_can_xstats(text):
    """从 `ip -details -statistics` 的文本里抠 CAN 专用计数。

    返回 dict；一个都没抠到就返回空 dict（**不返回假值** ——
    "读不到"和"全部为 0"是两件事，前者要人去查原因，后者是健康）。
    """
    if not text:
        return {}
    found = {}
    lines = text.splitlines()
    for pos, line in enumerate(lines):
        tokens = line.split()
        # iproute2 通常输出表头与下一行计数；也兼容 key value 的输出。
        if pos + 1 < len(lines) and tokens and all(t in CAN_XSTAT_KEYS for t in tokens):
            values = lines[pos + 1].split()
            for key, value in zip(tokens, values):
                if value.isdigit():
                    found[key] = int(value)
        for i, token in enumerate(tokens[:-1]):
            key = token.rstrip(":")
            if key in CAN_XSTAT_KEYS and tokens[i + 1].isdigit():
                found[key] = int(tokens[i + 1])
    if "arbit-lost" in found:
        found["arbitration-lost"] = found.pop("arbit-lost")
    return found


def cmd_bus_stats(args, out):
    """--bus-stats：接口现状、netlink 错误计数、模块与实时进程状态（只读）。

    只读的含义在这里是**连一帧都不发**：不打开 AF_CAN 套接字，
    只调 `ip` 与 systemctl、只读 sysfs 的一个 operstate。因此它任何身份可跑，
    也不受 km3-rt-controller 状态限制。
    """
    cfg, cfg_path = load_device_json()
    item, _ = find_can_bus(cfg, LOGICAL_BUS)
    iface = item.get("iface", "")
    out.title("总线状态 {}（{}）".format(LOGICAL_BUS, iface or "未配置"))
    out.info("只读子命令：不发帧、不开套接字，不要求 --i-know-rt-is-running。")
    out.field("配置文件", cfg_path)

    if not iface:
        out.bad("配置里 {} 的 iface 为空。".format(LOGICAL_BUS))
        return 2

    actual = read_iface_actual(iface)
    if actual is None:
        out.warn("接口 {} 不存在。".format(iface))
        out.info("可能是 {} 未运行，或 SPI-CAN 驱动未加载。".format(CAN_SERVICE))
        out.info("驱动模块名以设备树 compatible 为准 —— 可用下面的 kmod 一行核对。")
        out.field("kmod", probe_kernel_module(iface))
        return 1
    if actual.get("error"):
        out.warn(actual["error"])
        return 1

    out.field("state", actual.get("state"))
    out.field("CAN state", actual.get("can_state") or "unknown")
    out.field("flags", actual.get("flags"))
    cur = actual.get("bitrate")
    out.field("bitrate", "{} bps".format(cur) if cur is not None else "(未设置)")
    declared = item.get("bitrate")
    out.field("配置声明", "{} bps".format(declared) if declared else "missing")
    if declared and cur is not None and cur != declared:
        out.warn("位速率与配置不一致：现状 {} != 配置 {}".format(cur, declared))
        out.info("配置是 /etc/km3/device.json；运行时生效值来自 {} 的 CAN_BITRATE=".format(
            CAN_ENV))

    # 内核模块与实时进程：现场排障最常问的两条，一并放这里。
    out.field("kmod", probe_kernel_module(iface))
    rt_state = service_active(RT_SERVICE)
    out.field("{}".format(RT_SERVICE),
              {True: "active（已在用这条总线）", False: "inactive",
               None: "未知（systemctl 不可用）"}[rt_state])

    stats = parse_ip_stats(iface)
    if "error" in stats:
        out.warn(stats["error"])
    else:
        print()
        for k in ("rx_packets", "rx_errors", "rx_dropped",
                  "tx_packets", "tx_errors", "tx_dropped"):
            if k in stats:
                out.field(k, stats[k])

        # CAN 专用计数：这里是本子命令存在的主要理由。
        xstats = parse_can_xstats(stats.get("raw", ""))
        got = {k: v for k, v in xstats.items() if k in CAN_XSTAT_REQUIRED}
        missing = [k for k in CAN_XSTAT_REQUIRED if k not in got]
        print()
        if got:
            for k in CAN_XSTAT_REQUIRED:
                if k in got:
                    out.field("can/" + k, got[k])
            for k in sorted(set(xstats) - set(CAN_XSTAT_REQUIRED)):
                out.field("can/" + k, xstats[k])
        if missing:
            out.warn("未取到 CAN 专用计数：{}".format(", ".join(missing)))
            out.info("这些计数**不在 sysfs**（sysfs 的 rx_errors 对 CAN 恒为 0），")
            out.info("只能从 netlink 的 IFLA_INFO_XSTATS 取，由 `ip -details -statistics` 渲染。")
            out.info("取不到时**不拿通用 rx_errors 冒充** —— 那会让缺终端电阻的")
            out.info("症状（错误计数增长、应用层无报错）看起来像健康。")
            out.info("手工核对：ip -details -statistics link show <接口>")

        # 判读提示：ERROR-ACTIVE 是最容易被误判的一条。
        print()
        out.info("判读：ERROR-ACTIVE 是**正常活动态**，不是故障 ——")
        out.info("它只描述控制器错误状态，不能据此判断是否有对端应答。")
        out.info("要看的是上面三个错误计数是否在**增长**。")

    if args.show_raw:
        print()
        print(stats.get("raw", ""))
    return 0


def probe_kernel_module(iface):
    """哪个内核模块提供了这个接口。信息不足时如实说"未知"。

    只读 /sys/class/net/<if>/device/driver/module —— 归 km3-can.service 管的
    是接口状态，模块是驱动的事，两件事分开报。
    """
    if not iface:
        return "(无接口)"
    link = "/sys/class/net/{}/device/driver/module".format(iface)
    try:
        if os.path.exists(link):
            return os.path.basename(os.path.realpath(link))
    except OSError:
        pass
    # 没有 device/ 的接口（vcan、回环）走到这里：报"未知"而不是编一个名字。
    return "(未知：无 device/driver/module)"


def cmd_raw(args, out):
    """--raw：只监听，永不发送。"""
    cfg, _ = load_device_json()
    item, _ = find_can_bus(cfg, LOGICAL_BUS)
    iface = item.get("iface", "")
    out.title("原始帧监听 {}（{}）".format(LOGICAL_BUS, iface or "未配置"))
    out.info("只读子命令：只监听，不发任何帧。")
    if not iface:
        out.bad("配置里 can-2 的 iface 为空。")
        return 2
    try:
        bus = connect_bus(iface, out)
    except ConfigError as exc:
        out.bad(str(exc))
        return 3

    import time
    count = 0
    start = time.monotonic()
    duration = args.duration or (args.listen_ms / 1000.0 if not args.count else 0)
    try:
        while True:
            if duration and (time.monotonic() - start) >= duration:
                break
            remaining = duration - (time.monotonic() - start) if duration else 0.5
            f = bus.recv(timeout=min(0.5, max(0.0, remaining)))
            if f is None:
                continue
            print("  {:>12.6f}  {}".format(time.monotonic() - start, format_frame(f)))
            count += 1
            if args.count and count >= args.count:
                break
    except KeyboardInterrupt:
        out.info("中断。")
    finally:
        bus.close()
    print()
    out.field("收到帧数", count)
    return 0


def cmd_test(args, out):
    """严格离线自检：配置、声明、共用协议层模拟节点；不开真实套接字。"""
    out.title("离线自检（不开真实总线、不发现场帧）")
    failures = 0
    try:
        cfg, path = load_device_json()
        item, _ = find_can_bus(cfg, LOGICAL_BUS)
        out.ok("配置映射可用：{} -> {}（{}）".format(LOGICAL_BUS, item["iface"], path))
    except ConfigError as exc:
        out.bad(str(exc))
        failures += 1
    try:
        od = load_od()
        pending = [e["key"] for e in od["entries"] if e["status"] != "verified"]
        out.ok("对象字典 {} 条，{} 条未核对；运动闸门{}".format(
            len(od["entries"]), len(pending), "关闭" if pending else "已核对"))
        if pending:
            out.info("未核对：{}".format(", ".join(pending)))
    except ConfigError as exc:
        out.bad(str(exc))
        failures += 1
    if not require_std_layer("--test 离线协议自检"):
        failures += 1
    else:
        try:
            import mockbus
            bus = mockbus.MockBus([mockbus.MockNode(1)], loopback=False)
            try:
                if _canopen.scan_nodes(bus, nodes=(1, 2), timeout=0.01) != [1]:
                    raise ValueError("模拟扫描未区分应答与静默节点")
                identity = _canopen.SdoClient(bus, 1, timeout=0.01).read_identity()
                if identity.get("count") != 4 or "serial_number" not in identity:
                    raise ValueError("模拟身份对象缺少子对象")
                out.ok("共用协议层 MockBus：节点扫描、静默节点、身份子对象回读通过")
            finally:
                bus.close()
        except (ImportError, ValueError, _canopen.CanopenError) as exc:
            out.bad("离线协议自检失败：{}".format(exc))
            failures += 1
    out.info("该结果是离线检查；物理控制器回环、板端与电机验收仍需另行执行。")
    if failures:
        out.bad("自检未通过（{} 项）".format(failures))
        return 1
    out.ok("离线自检通过")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    epilog = (
        "物理标识（内核接口名）只在 device/config/device.json 里定义，\n"
        "本工具按逻辑总线名 {bus} 解析，不接受命令行直接指定。\n"
        "\n"
        "示例：\n"
        "  python3 pmp4010.py --init --verify        # 纯只读，看现状与差异\n"
        "  python3 pmp4010.py --init                 # 写 CAN_BITRATE 并打印 root 命令\n"
        "  python3 pmp4010.py --bus-stats            # 接口 + CAN 错误计数 + kmod\n"
        "  python3 pmp4010.py --od-dump              # 对象字典声明与核对状态\n"
        "  python3 pmp4010.py --scan --node-range 1-4 --identity   # SDO 扫节点\n"
        "  python3 pmp4010.py --raw --count 20       # 只监听，抓 20 帧\n"
        "  python3 pmp4010.py --nmt --nmt-command start --node 1 --i-know-rt-is-running\n"
        "  python3 pmp4010.py --motion stop --node 1 # 快速停机（需 OD 全部核对）\n"
        "\n"
        "更稳的运行方式（自动接好共用外壳的 sys.path，与当前目录无关）：\n"
        "  {run}\n"
    ).format(bus=LOGICAL_BUS,
             run=discover_hint() or
                 "python3 tools/hwtui/discover.py --run {}".format(discover_tool_path()))

    p = argparse.ArgumentParser(
        prog="pmp4010.py",
        description="PMP4010（机身抬降电机 A）板载调试工具 —— curses TUI 与一次性子命令。\n"
                    "纯只读子命令任何身份可跑；--scan 发 SDO 上传请求；"
                    "--nmt/--motion 是运动类，受对象字典闸门限制。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog)

    # required=False：不带子命令时进 TUI 概览面板（与模板 tools/<代号>.py 同一行为）。
    # 板端现场最常见的用法就是"先 ssh 上去看一眼"，那不该逼人挑一个子命令。
    action = p.add_mutually_exclusive_group(required=False)
    action.add_argument("--init", action="store_true",
                        help="三段式初始化：读配置与现状、写 {}，打印 root 命令".format(CAN_ENV))
    action.add_argument("--test", action="store_true",
                        help="严格离线自检：配置、对象字典、共用协议 MockBus；不开真实总线")
    action.add_argument("--scan", action="store_true",
                        help="主动 SDO 扫节点 1..127（只发上传请求，零写请求；受 RT 确认限制）")
    action.add_argument("--nmt", action="store_true", help="发送 NMT 命令（发帧）")
    action.add_argument("--od-dump", action="store_true",
                        help="打印对象字典声明与核对状态（只读）")
    action.add_argument("--motion", choices=["enable", "disable", "stop", "home"],
                        help="CiA402 使能/失能/快停/回零（发帧）；需 OD 全部核对与显式 --node")
    action.add_argument("--bus-stats", action="store_true",
                        help="接口现状、CAN 专用错误计数、kmod、实时进程（只读）")
    action.add_argument("--raw", action="store_true", help="只监听原始帧（只读）")

    p.add_argument("--bus", choices=[LOGICAL_BUS], default=LOGICAL_BUS,
                   help="逻辑总线；实际接口与位速率仅从 device.json 解析")
    p.add_argument("--timeout", type=float, default=0.05,
                   help="--scan/--motion 单次 SDO 应答超时秒数（默认 0.05，主机策略）")
    p.add_argument("--state-timeout", type=float, default=2.0,
                   help="--motion 每次状态迁移超时秒数（默认2，主机策略）")
    p.add_argument("--home-timeout", type=float, default=10.0,
                   help="--motion home 启动后完成期限秒数（默认10，主机策略）")
    p.add_argument("--homing-method", type=int, choices=list(range(17, 31)) + [35], default=17,
                   help="--motion home 回零方法（默认17=负限位；35=当前位置置零）。"
                        "须预先配置对应输入、速度、加速度；不支持任务示例中的方法1")
    p.add_argument("--verify", "--read-only", dest="verify", action="store_true",
                   help="只对 --init 有意义：纯只读，不修改任何文件")
    p.add_argument("--no-tui", action="store_true",
                   help="不启用 curses 概览屏")
    p.add_argument("--i-know-rt-is-running", dest="i_know_rt_is_running",
                   action="store_true",
                   help="确认已知 {} 正在使用总线；发帧类子命令必需".format(RT_SERVICE))
    p.add_argument("--nmt-command", default="start",
                   choices=["start", "stop", "preop", "reset-node", "reset-comm"],
                   help="--nmt 的命令（默认 start）")
    p.add_argument("--node", type=int, default=None,
                   help="目标节点号 0..127；NMT 省略或给 0 则为广播")
    p.add_argument("--node-range", default=None,
                   help="--scan 的节点号范围，如 1-4 或 1。省略则扫 1..127")
    p.add_argument("--identity", action="store_true",
                   help="--scan 时额外读设备类型、名称、软件版本、身份对象各子项")
    p.add_argument("--rows", type=int, default=None,
                   help="TUI 行数（板端 stty size 返回 0 0 时的兜底，见 tui.resolve_size）")
    p.add_argument("--cols", type=int, default=None,
                   help="TUI 列数（同上）")
    p.add_argument("--ascii", action="store_true",
                   help="TUI 用纯 ASCII 边框（CJK locale 下制表符号可能被判成双宽）")
    p.add_argument("--listen-ms", type=int, default=2000,
                   help="--raw 默认监听时长毫秒数（默认 2000；--count/--duration 优先）")
    p.add_argument("--count", type=int, default=0,
                   help="--raw 收够多少帧后退出（0 = 不限）")
    p.add_argument("--duration", type=int, default=0,
                   help="--raw 持续多少秒后退出（0 = 不限）")
    p.add_argument("--show-raw", action="store_true",
                   help="--bus-stats 时额外打印 ip 命令的原始输出")
    p.add_argument("--dump-json", action="store_true",
                   help="--od-dump 时额外打印完整 JSON")
    return p


def cmd_panel(args, out):
    """不带子命令：画一屏概览（纯只读，不开套接字、不发帧）。

    数据在进 curses **之前**读好：curses 里做 I/O 会把画面画成挤牙膏，
    而且读失败时没法降级成文本。读失败照样显示那一行（值为"读取失败"），
    不因为一个字段读不到就整屏不画 —— 面板的价值恰恰在于"现在的现状是什么"。
    """
    try:
        cfg, cfg_path = load_device_json()
    except ConfigError as exc:
        out.bad(str(exc))
        return 3
    try:
        item, _ = find_can_bus(cfg, LOGICAL_BUS)
        iface = item.get("iface", "")
    except ConfigError as exc:
        out.bad(str(exc))
        return 3

    actual = read_iface_actual(iface) if iface else None
    rows = panel_rows(iface, cfg_path, actual, item.get("bitrate"),
                      service_active(RT_SERVICE))
    header = "PMP4010 机身抬降电机 A — 逻辑名 {}（只读面板）".format(LOGICAL_BUS)
    shown = HwPanel(rows, enable=not args.no_tui,
                    rows_forced=args.rows, cols_forced=args.cols,
                    box="ascii" if args.ascii else None).show(panel_lines(rows, header))
    if not shown:
        # 纯文本路径：面板画不了就退回逐行打印。已经由 panel_lines 排好版。
        pass
    print()
    out.info("面板只读：不开套接字、不发帧、不改文件。")
    out.info("要扫节点请用 --scan；要看错误计数请用 --bus-stats。")
    hint = discover_hint()
    if hint:
        out.info("运行建议：{} --test".format(hint))
    return 0


def main(argv=None):
    # 行缓冲（开发规范 §1.5 的同一条规矩）：被 tee / journal 捕获时，
    # 十几秒的 --raw 若一直没输出，现场只能靠猜工具是不是卡了。
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.verify and not args.init:
        parser.error("--verify/--read-only 仅可与 --init 一起使用")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout 必须为有限正数")
    if any(not math.isfinite(value) or value <= 0
           for value in (args.state_timeout, args.home_timeout)):
        parser.error("--state-timeout/--home-timeout 必须为有限正数")
    if args.listen_ms <= 0 or args.count < 0 or args.duration < 0:
        parser.error("--listen-ms 必须为正数；--count/--duration 不能为负数")
    if args.node is not None and not 0 <= args.node <= 127:
        parser.error("--node 必须在 0..127")
    if args.node_range:
        try:
            parse_node_range(args.node_range)
        except ValueError as exc:
            parser.error(str(exc))
    out = Out()

    try:
        if args.init:
            return cmd_init(args, out)
        if args.test:
            return cmd_test(args, out)
        if args.scan:
            return cmd_scan(args, out)
        if args.nmt:
            return cmd_nmt(args, out)
        if args.motion:
            return cmd_motion(args, out)
        if args.od_dump:
            return cmd_od_dump(args, out)
        if args.bus_stats:
            return cmd_bus_stats(args, out)
        if args.raw:
            return cmd_raw(args, out)
        return cmd_panel(args, out)
    except (ConfigError, MotionError) as exc:
        out.bad(str(exc))
        return 3
    except KeyboardInterrupt:
        out.info("中断。")
        return 130
    except ValueError as exc:
        out.bad(str(exc))
        return 2
    except Exception as exc:
        if ((_canbus is not None and isinstance(exc, _canbus.CanBusError)) or
                (_canopen is not None and isinstance(exc, _canopen.CanopenError))):
            out.bad(str(exc))
            return 3
        raise




if __name__ == "__main__":
    sys.exit(main())
