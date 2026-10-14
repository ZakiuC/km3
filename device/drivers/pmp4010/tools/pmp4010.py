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

子命令分两类，权限要求不同：

  只读（任何身份可跑，不受 km3-rt-controller 状态限制）
    --scan       被动监听总线，看谁在说话
    --od-dump    打印对象字典声明
    --bus-stats  接口现状与统计
    --raw        只监听，抓原始帧

  发帧（km3-rt-controller 处于 active 时要求 --i-know-rt-is-running）
    --init       写目标位速率（见下）
    --nmt        发 NMT 命令

--init 是三段式，**顺序不可颠倒**，每段都对应一条实测事实：
  (1) 读 /etc/km3/device.json 取 can-2 的 iface 与 bitrate，再用
      `ip -details link show` 读现状，不一致就报差异（只报告，不改）；
  (2) 写目标位速率到 /etc/km3/can.env 的 CAN_BITRATE=（km3 拥有该文件，
      0640 km3:km3，无需提权）；
  (3) 打印逐字可复制的 `sudo systemctl restart km3-can`，并说明原因。
--init --verify 是纯只读的：做 (1) 与 (3) 的展示，不做 (2)。
"""

import argparse
import errno
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEVICE_JSON = "/etc/km3/device.json"
# 兜底路径：置备前的板子上 /etc/km3/device.json 可能还不存在。
DEVICE_JSON_FALLBACKS = ["/opt/km3/current/device.json"]
CAN_ENV = "/etc/km3/can.env"
CAN_SERVICE = "km3-can.service"
RT_SERVICE = "km3-rt-controller.service"

# 逻辑名。CAN-2 = 主板第二路 CAN —— 逻辑名与内核接口名"下标差一位"，
# 正是这一层存在的理由，所以这里只写逻辑名。
LOGICAL_BUS = "can-2"

# SocketCAN 常量。用字面值而非 python-can：板上不保证有 python-can，
# 而这些数字是内核 ABI 的一部分（<linux/can.h>），比第三方包更稳定。
PF_CAN = 29
CAN_RAW = 1
CAN_MTU = 16
CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_SFF_MASK = 0x000007FF

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
#   - 按需导入，失败时退化到本文件内置的最小实现，并在输出里**明说**
#     "标准层不可用，正在使用内置降级实现" —— 静默降级会让人以为
#     自己用的是被审计过的那条路径。
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
    os.path.join(_REPO_ROOT, "current", "hwtui"),
    os.path.join(os.path.dirname(_HERE), "..", "..", "hwtui"),
)
for _p in _STD_CANDIDATES:
    _p = os.path.abspath(_p)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)   # append 而非 insert：不要遮蔽调用方自己的模块


def _load_std_layer():
    """按需导入 tools/hwtui 的标准层。返回 True 表示可用。

    两种导入形态都试：先按包（hwtui.canbus），再按扁平模块（canbus）。
    后者是板上部署后的形态，也是 hwtui 内部互相引用的形态。
    """
    global _canbus, _canopen, _HWTUI_ERROR
    if _canbus is not None and _canopen is not None:
        return True
    errors = []
    for importer in ("package", "flat"):
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
    """取逻辑名对应的 can 项。返回 (item, 所在数组名)。"""
    bus = cfg.get("bus", {})
    for key in ("can", "usb_can"):
        for item in bus.get(key, []):
            if item.get("logical") == logical:
                return item, key
    raise ConfigError("配置里没有逻辑名为 {!r} 的 CAN 总线".format(logical))


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

    return {"state": state, "bitrate": bitrate, "flags": flags, "raw": out, "error": None}


def parse_ip_stats(iface):
    """读接口统计（`ip -s -details link show`）。

    注意：CAN 专用的错误计数（bus-off 次数、错误帧计数）**不在 sysfs**，
    只能从 netlink 的 IFLA_INFO_XSTATS 取。本工具当前从 `ip -details` 的
    文本里尽力提取，取不到就明确说取不到 —— 不拿通用的 rx_errors 冒充。
    """
    rc, out, err = run_readonly(["ip", "-s", "-details", "link", "show", iface])
    if rc != 0:
        return {"error": err.strip() or "ip 返回 {}".format(rc)}
    stats = {"raw": out}
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("RX:"):
            for k, v in zip(["rx_bytes", "rx_packets", "rx_errors", "rx_dropped",
                             "rx_missed"], s.split()[1:]):
                if v.isdigit():
                    stats[k] = int(v)
        elif s.startswith("TX:"):
            for k, v in zip(["tx_bytes", "tx_packets", "tx_errors", "tx_dropped",
                             "tx_carrier"], s.split()[1:]):
                if v.isdigit():
                    stats[k] = int(v)
    can_err = {}
    for line in out.splitlines():
        for key in ("bus-error", "bus-errors", "arbitration-lost", "error-warn",
                    "error-pass", "restart-ms", "re-started"):
            if key in line:
                m = re.search(re.escape(key) + r"[:\s]+(\d+)", line)
                if m:
                    can_err[key] = int(m.group(1))
    if can_err:
        stats["can_specific"] = can_err
    return stats


# ---------------------------------------------------------------------------
# SocketCAN 原始套接字（收发唯一路径）
# ---------------------------------------------------------------------------

def iface_index(iface):
    """接口名 → 内核下标。失败返回 None。"""
    if not iface:
        return None
    try:
        return socket.if_nametoindex(iface)
    except OSError:
        return None


def open_can_socket(iface, filters=None):
    """打开并绑定到 iface 的 CAN 原始套接字（内置实现）。

    这是**降级路径**：标准层可用时不会走到这里（见 connect_bus）。

    失败时报错要能指导现场动作，所以按 errno 分情况说明：
      ENODEV -> 接口不存在（多半是内核接口还没被 km3-can 拉起）
      EPERM  -> 权限不足（不是"接口不存在"）
      EINVAL -> 名字不对或内核不支持
    把三者混成一句"打开失败"，会让现场排查从猜开始。
    """
    idx = iface_index(iface)
    if idx is None:
        raise ConfigError(
            "接口 {} 不存在。请先确认 {} 已把它拉起：systemctl status {}".format(
                iface, CAN_SERVICE, CAN_SERVICE))
    try:
        sock = socket.socket(PF_CAN, socket.SOCK_RAW, CAN_RAW)
    except OSError as exc:
        raise ConfigError(
            "无法创建 CAN 套接字：{}。内核可能未编入 CAN 支持"
            "（CONFIG_CAN / CONFIG_CAN_RAW，见 /etc/modules-load.d/km3.conf）。".format(exc))
    try:
        # sockaddr_can { u16 can_family; i32 can_ifindex; u32 rx_id; u32 rx_mask }
        # 这里只用到前两项，末尾补零即可（内核不读未用部分）。
        sock.bind((idx, 0))
    except OSError as exc:
        sock.close()
        code = errno.errorcode.get(exc.errno, str(exc.errno))
        hint = {
            "EPERM": "权限不足：需要 CAP_NET_RAW",
            "EACCES": "权限不足：需要 CAP_NET_RAW",
            "ENODEV": "接口不存在",
        }.get(code, "未知原因")
        raise ConfigError("绑定 {} 失败：{}（{}）".format(iface, hint, code))
    return sock


class _RawBus:
    """内置降级总线的薄包装，暴露与 canbus.CanBus 相同的 send/recv/close。

    为什么要包一层而不是直接用裸套接字：调用点只认 send(frame)/recv(timeout)，
    两种实现（标准层与降级）必须同形，否则"标准层不可用时行为不同"会变成
    一个只在板端偶发的问题。
    """

    def __init__(self, iface):
        self.interface = iface
        self._sock = open_can_socket(iface)

    def send(self, frame):
        """frame 需有 id / data（bytes）/ extended / remote 三个属性。"""
        data = bytes(frame.data)
        if len(data) > 8:
            raise ValueError("经典 CAN 帧载荷上限 8 字节，收到 {} 字节".format(len(data)))
        flags = 0
        if getattr(frame, "extended", False):
            flags |= CAN_EFF_FLAG
        if getattr(frame, "remote", False):
            flags |= CAN_RTR_FLAG
        payload = data + b"\x00" * (8 - len(data))
        return self._sock.send(
            struct.pack("=IB3x8s", frame.id | flags, len(data), payload))

    def recv(self, timeout=None):
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            raw = self._sock.recv(CAN_MTU)
        except socket.timeout:
            return None
        except OSError as exc:
            raise ConfigError(str(exc))
        can_id, dlc, data = struct.unpack("=IB3x8s", raw[:CAN_MTU])
        extended = bool(can_id & CAN_EFF_FLAG)
        return _SimpleFrame(
            id=can_id & (0x1FFFFFFF if extended else CAN_SFF_MASK),
            data=data[:min(dlc, 8)],
            extended=extended,
            remote=bool(can_id & CAN_RTR_FLAG),
        )

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


class _SimpleFrame:
    """降级路径的帧对象。字段对齐 canopen.Frame，让上层写法只有一种。"""

    __slots__ = ("id", "data", "extended", "remote")

    def __init__(self, id, data, extended=False, remote=False):
        self.id = id
        self.data = data
        self.extended = extended
        self.remote = remote

    def __repr__(self):
        return "Frame(id=0x%X, data=%s)" % (self.id, self.data.hex())


def connect_bus(iface, out=None):
    """连上一条总线。优先走标准层，失败则降级并在屏幕上说明。

    返回对象统一暴露 send(frame) / recv(timeout) / close()。
    """
    if _load_std_layer():
        try:
            bus = _canbus.CanBus(iface)
            bus.open()
            if out is not None:
                out.info("总线实现：tools/hwtui/canbus.py（标准层）")
            return bus
        except Exception as exc:               # noqa: BLE001 - 报错要原样带出去
            # 标准层存在的意义之一就是把 errno 翻译成人话，因此它的报错
            # 直接透传；这里只在它自己坏掉（非 CanBusError）时才提降级。
            if _canbus is not None and isinstance(exc, _canbus.CanBusError):
                raise ConfigError(str(exc))
            if out is not None:
                out.warn("标准层打开失败（{}），退化到内置实现".format(exc))
    elif out is not None:
        out.warn("tools/hwtui 不可用（{}），使用内置降级实现".format(_HWTUI_ERROR))
        out.info("降级实现只做 SocketCAN 收发，不含 CANopen 状态机。")
    try:
        return _RawBus(iface)
    except ConfigError:
        raise
    except OSError as exc:
        raise ConfigError("打开 {} 失败：{}".format(iface, exc))


def frame_can_id(f):
    """取帧的仲裁 ID（CANopen 里就是 COB-ID）。

    两种帧对象字段名不同，差异收在这一个函数里：
      - 标准层 canopen.Frame 用 `arbitration_id`（标志位另存，不混进 ID）
      - 本文件降级用的 _SimpleFrame 用 `id`
    差异不收在一处的话，每次读 COB-ID 都要在两个名字之间猜一次。
    """
    if hasattr(f, "arbitration_id"):
        return f.arbitration_id
    return getattr(f, "id", 0)


def format_frame(f):
    """把帧对象格式化成一行。兼容标准层的 canopen.Frame 与降级的 _SimpleFrame。"""
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
    if rc == 127:
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
    return active_state == "active"


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
    values, lines, existed = read_can_env()
    target = "CAN_BITRATE={}".format(bitrate)

    replaced = False
    out_lines = []
    for line in lines:
        s = line.strip()
        if s.startswith("CAN_BITRATE=") and not s.startswith("#"):
            if not replaced:
                out_lines.append(target)
                replaced = True
            continue
        out_lines.append(line)
    if not replaced:
        out_lines.append(target)

    text = "\n".join(out_lines) + "\n"
    try:
        with open(CAN_ENV, "w", encoding="utf-8") as fh:
            fh.write(text)
    except PermissionError:
        raise ConfigError(
            "{} 不可写。该文件为 km3:km3 0640；请以 km3 身份运行本工具"
            "（或先由置备脚本调整权限）。".format(CAN_ENV))
    except OSError as exc:
        raise ConfigError("{} 写入失败：{}".format(CAN_ENV, exc))
    return existed, values.get("CAN_BITRATE")


# ---------------------------------------------------------------------------
# curses TUI
#
# 板上的两个实测约束决定了这里的写法：
#   1. stty size 返回 0 0 —— 所以**不能**用 `stty size` 取窗口尺寸，
#      一律用 curses 自己的 getmaxyx()，并对其返回 0 做兜底。
#   2. tput colors=256 —— 颜色够用，但 TUI 不依赖颜色表达状态
#      （现场可能 ssh 到不支持 256 色的终端）。
# ---------------------------------------------------------------------------

class Tui:
    """curses 界面。离开时一定恢复终端（含异常路径）。

    为什么用 context manager：早期脚本在 curses 中途抛异常后没 endwin()，
    终端留在 raw 模式，现场表现是"ssh 卡死、看不到回显" ——
    一个调试工具把人困住，比它不工作更糟。
    """

    def __init__(self, enable=True):
        self.enabled = bool(enable) and sys.stdout.isatty()
        self.stdscr = None
        self.curses = None

    def __enter__(self):
        if not self.enabled:
            return self
        try:
            import curses
        except ImportError:
            self.enabled = False
            return self
        self.curses = curses
        try:
            self.stdscr = curses.initscr()
            curses.noecho()
            curses.cbreak()
            self.stdscr.keypad(True)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
        except Exception:
            # 初始化失败也要还原：initscr() 可能已经改了终端模式。
            self.__exit__(None, None, None)
            self.enabled = False
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.stdscr is not None and self.curses is not None:
            try:
                self.stdscr.keypad(False)
                self.curses.nocbreak()
                self.curses.echo()
                self.curses.endwin()
            finally:
                self.stdscr = None
        return False

    def size(self):
        """窗口尺寸。

        不用 `stty size`：板上它返回 0 0（实测）。curses 的 getmaxyx()
        是唯一可信来源，但它也可能返回 0；那时退到环境变量，最后退到 80x24。
        """
        if self.stdscr is not None and self.curses is not None:
            try:
                h, w = self.stdscr.getmaxyx()
                if h > 0 and w > 0:
                    return h, w
            except Exception:
                pass
        try:
            size = os.get_terminal_size()
            if size.lines > 0 and size.columns > 0:
                return size.lines, size.columns
        except OSError:
            pass
        return 24, 80

    def render(self, lines):
        """画一屏概览。调用方随后继续用普通 print 输出详情 ——
        分工是：curses 负责"扫一眼就懂"，print 负责可复制、可重放的文本。
        """
        if not self.enabled or self.stdscr is None:
            return
        curses = self.curses
        h, w = self.size()
        try:
            self.stdscr.erase()
            for i, line in enumerate(lines[:h - 1]):
                self.stdscr.addnstr(i, 0, line, max(w - 1, 1))
            self.stdscr.refresh()
        except curses.error:
            # 屏幕太小、或写到最后一格右下角：curses 会抛 error。
            # 这不是故障，忽略即可，但**不能**因此吞掉整个渲染导致空屏。
            pass

    def wait_key(self, prompt="按任意键继续"):
        if not self.enabled or self.stdscr is None:
            return None
        curses = self.curses
        h, w = self.size()
        try:
            self.stdscr.addnstr(h - 1, 0, prompt, max(w - 1, 1))
            self.stdscr.refresh()
            ch = self.stdscr.getch()
        except curses.error:
            return None
        if ch == 3:  # Ctrl-C：在 raw 模式下不会自动变成 KeyboardInterrupt
            raise KeyboardInterrupt
        return ch


def status_lines(iface, cfg_path, actual, declared_bitrate):
    """构造 TUI 概览行。纯函数，便于无终端时也走同一条路径。"""
    if actual is None:
        state_text = "不存在"
    elif actual.get("error"):
        state_text = "读取失败 ({})".format(actual["error"])
    else:
        rate = actual.get("bitrate")
        state_text = "state={} bitrate={}".format(
            actual.get("state"), rate if rate is not None else "(未设置)")
    return [
        "PMP4010 机身抬降电机 A  —  逻辑名 {}  配置 {}".format(LOGICAL_BUS, cfg_path),
        "",
        "内核接口      : {}".format(iface or "(未配置)"),
        "接口现状      : {}".format(state_text),
        "配置声明速率  : {} bps".format(declared_bitrate),
        "",
        "本工具不接管接口状态；up/down 与位速率归 {}".format(CAN_SERVICE),
        "发帧类子命令在 {} active 时需 --i-know-rt-is-running".format(RT_SERVICE),
    ]


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
        out.warn("位速率：配置声明 {} != {} 中的 {}（下面第 2 步会改写成声明值）".format(
            bitrate, CAN_ENV, env_bitrate))

    if not args.no_tui:
        with Tui(enable=True) as tui:
            if tui.enabled:
                tui.render(status_lines(iface, cfg_path, actual, bitrate))
                tui.wait_key()

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
            "已更新" if existed else "已创建", bitrate,
            old if old is not None else "无"))
        out.info("该文件由 km3 拥有（0640 km3:km3），本次写入无需提权。")
        out.info("文件内其余行（CAN_IFACES 等现场参数）未被改动。")

    out.title("3/3 需要 root 执行的一步（本工具不代劳）")
    out.info("接口状态归 {} 所有，本工具不接管。".format(CAN_SERVICE))
    out.info("km3 用户 CapEff=0：`ip link set` 报 Operation not permitted，")
    out.info("`systemctl restart` 报 Interactive authentication required。")
    out.info("因此下面这行请由人执行，逐字可复制：")
    out.cmd("sudo systemctl restart {}".format(CAN_SERVICE))
    out.info("执行后可用 `python3 {} --bus-stats` 复查现状。".format(
        os.path.basename(sys.argv[0])))
    return 0


def cmd_scan(args, out):
    """--scan：被动监听总线，看谁在说话（只读）。

    **不发任何帧。** 真枚举节点要靠 SDO 读取，而那需要已验证的对象字典
    （od.json 现在全是 null），所以本子命令不假装能枚举 ——
    它报告"总线上现在有谁在说话"，这在调试接线与波特率时已经够用。
    """
    cfg, _ = load_device_json()
    item, _ = find_can_bus(cfg, LOGICAL_BUS)
    iface = item.get("iface", "")
    out.title("监听 {}（{}）".format(LOGICAL_BUS, iface or "未配置"))
    out.info("只读子命令：不发任何帧，不要求 --i-know-rt-is-running。")

    if not iface:
        out.bad("配置里 can-2 的 iface 为空，无处可听。")
        return 2

    # 节点号范围：给出时用配置里的 4 台节点号过滤显示；
    # 没给就全显示并说明"节点号分配方式未确认"。
    wanted = None
    if args.node_range:
        lo, hi = parse_node_range(args.node_range)
        wanted = set(range(lo, hi + 1))
        out.field("关注范围", "{}-{}".format(lo, hi))
    else:
        out.info("未给 --node-range，显示所有节点。节点号分配方式未确认")
        out.info("（docs/硬件手册/PMP4010/README.md 条目 D9），本工具不设默认范围。")

    try:
        bus = connect_bus(iface, out)
    except ConfigError as exc:
        out.bad(str(exc))
        return 3

    seen = {}
    try:
        out.info("被动监听 {} ms ...".format(args.listen_ms))
        elapsed = 0.0
        step = 0.05
        while elapsed < args.listen_ms / 1000.0:
            f = bus.recv(timeout=step)
            elapsed += step
            if f is None:
                continue
            # CANopen 预定义连接集里，COB-ID 携带节点号：
            #   0x080 SYNC（广播）/ 0x080+node EMCY / 0x180+node TPDO1
            #   0x200+node RPDO1 / 0x580+node SDO 应答 / 0x700+node 心跳
            # 用低 7 位取节点号只对这几段成立，广播帧（COB-ID < 0x080）没有节点号。
            # 节点号 → 范围的换算交给标准层（canopen 里已有 COB-ID 工具函数），
            # 这里只用"低 7 位"这个公共约定做粗筛，不做协议判断。
            node = None
            if frame_can_id(f) >= 0x081:
                node = frame_can_id(f) & 0x7F
            if wanted is not None and node is not None and node not in wanted:
                continue
            key = node
            seen.setdefault(key, [0, None])
            seen[key][0] += 1
            seen[key][1] = f
    except KeyboardInterrupt:
        out.info("中断。")
    finally:
        bus.close()

    print()
    if not seen:
        out.warn("{} ms 内未收到任何帧。".format(args.listen_ms))
        out.info("被动监听不等于扫描：从站在无人提问时通常不说话。")
        out.info("收到帧才说明总线活着；收不到既可能是没人说话，也可能是")
        out.info("位速率不符或接线问题 —— 这两种原因无法从「静默」区分开。")
        return 1

    out.field("发现节点数", len([n for n in seen if n is not None]))
    for key in sorted(seen, key=lambda k: (k is None, k)):
        count, last = seen[key]
        label = "节点 {:>3}".format(key) if key is not None else "广播/无节点号"
        out.ok("{}：{:>5} 帧   末帧 {}".format(label, count, format_frame(last)))
    return 0


def cmd_nmt(args, out):
    """--nmt：发 NMT 命令（广播单帧）。"""
    cfg, _ = load_device_json()
    item, _ = find_can_bus(cfg, LOGICAL_BUS)
    iface = item.get("iface", "")
    out.title("NMT {} -> {}".format(args.nmt_command, args.node if args.node else "广播"))

    # 顺序有讲究：**先**要人确认 rt-controller 的风险，**再**去做别的检查。
    # 早期版本把接口存在性放在前面，结果在接口缺失的环境里永远是那条
    # "接口不存在"先返回，准入确认一次都没被执行到 —— 一个看起来装了、
    # 实际从未生效的闸门。闸门必须在别的失败之前开口。
    if not require_rt_ack(args, out, "发送 NMT"):
        return 4

    if not iface:
        out.bad("配置里 can-2 的 iface 为空。")
        return 2

    commands = {"start": 0x01, "stop": 0x02, "preop": 0x80,
                "reset-node": 0x81, "reset-comm": 0x82}
    code = commands.get(args.nmt_command)
    if code is None:
        out.bad("未知 NMT 命令 {!r}".format(args.nmt_command))
        return 2
    node = args.node if args.node else 0
    if not 0 <= node <= 127:
        out.bad("节点号必须在 0..127（0 = 广播）")
        return 2

    out.info("注意：NMT 是广播命令，一次影响总线上**所有**匹配的从站。")
    out.info("上电默认状态未确认（手册条目 D10），Start 之后从站的行为未知。")
    try:
        bus = connect_bus(iface, out)
    except ConfigError as exc:
        out.bad(str(exc))
        return 3
    try:
        # NMT 帧的组装交给标准层（canopen.nmt_command_frame / NmtMaster）。
        # 本文件不自己拼 COB-ID 与载荷 —— 那是第 7 步「标准层归位」的要求。
        if std_layer_available():
            master = _canopen.NmtMaster(bus)
            frame = master.send_command(code, node)
            out.ok("已发送（经标准层 NmtMaster）：id=0x{:03X} data={}".format(
                frame_can_id(frame), bytes(frame.data).hex(" ").upper()))
        else:
            frame = _SimpleFrame(id=0x000, data=bytes([code, node]))
            bus.send(frame)
            out.ok("已发送（内置降级实现）：id=0x000 data=[{:02X} {:02X}]".format(code, node))
    finally:
        bus.close()
    return 0


def cmd_motion(args, out):
    """--motion：手工点动（发帧）。

    这个子命令的现状是**永远拒绝执行**，且这不是没写完：驱动侧
    kOdVerified 为 false（od.json 全部 index 为 null），一个字节都发不出去。
    工具不绕过它自己去拼一帧的原因很直接：
      - 控制字的索引与位定义未知 —— 拼出来的"使能"可能被从站解读成别的；
      - 位置与速度标度未知 —— 拼出来的数值没有物理意义；
      - 节点号分配未知 —— 不知道在跟谁说话。
    四条里任何一条单独成立，手工点动 4 台抬降电机都是拿硬件做实验。
    因此这里显式失败，并把阻塞项指出来，而不是留一个"以后补上"的空壳。
    """
    out.title("手工点动 {} 节点 {}".format(args.motion, args.node))
    if not require_rt_ack(args, out, "手工点动"):
        return 4

    out.bad("拒绝执行：PMP4010 的对象字典未验证（全部 index 为 null）。")
    out.info("控制字索引与位定义、位置/速度标度、节点号分配均已确认缺失。")
    out.info("资料清单与消解方式：docs/硬件手册/PMP4010/README.md")
    out.info("驱动侧守卫见 device/drivers/pmp4010/driver/pmp4010_driver.hpp")
    out.info("资料到位后本条子命令才实现，且实现前要先写清停机路径。")
    return 5


def cmd_od_dump(args, out):
    """--od-dump：打印对象字典声明（只读）。"""
    out.title("对象字典声明（driver/od.json）")
    path = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "driver", "od.json"))
    if not os.path.exists(path):
        out.bad("找不到 {}".format(path))
        out.info("本子命令读的是仓库内的声明文件；板端只部署工具时该文件可能不在。")
        out.info("驱动源码里同一个文件在 device/drivers/pmp4010/driver/od.json。")
        return 2

    with open(path, "r", encoding="utf-8") as fh:
        od = json.load(fh)

    out.info("只读子命令：不发帧，不要求 --i-know-rt-is-running。")
    out.info("以下内容**全部未验证** —— 厂家手册未提供，index 一律为 null。")
    out.info("驱动因此在编译期就被挡住，一个字节都不会发到总线上。")
    print()

    header = "{:<30} {:<11} {:<8} {:<6} {}".format("key", "status", "index", "sub", "cite")
    print("  " + out.c("bold", header))
    print("  " + out.c("dim", "-" * min(78, max(len(header), 40))))
    unverified = 0
    for e in od.get("entries", []):
        if e.get("status") == "unverified":
            unverified += 1
        print("  {:<30} {:<11} {:<8} {:<6} {}".format(
            e.get("key", "?"), e.get("status", "?"),
            "null" if e.get("index") is None else hex(e["index"]),
            "null" if e.get("sub") is None else e["sub"],
            "null" if e.get("cite") is None else e["cite"]))
    print()
    out.field("条目总数", len(od.get("entries", [])))
    out.field("未验证条目", unverified)

    device = od.get("device", {})
    print()
    out.field("从站型号", device.get("model", "?"))
    out.field("台数", device.get("units_on_bus", "?"))
    out.field("逻辑总线", device.get("logical_bus", "?"))
    out.field("协议", device.get("protocol", "?"))
    for key in ("node_id", "cia_profile", "power_on_state", "termination"):
        entry = device.get(key, {})
        if entry:
            out.field("{}".format(key),
                      "{}  ({})".format(entry.get("status", "?"),
                                        entry.get("notes", "")))

    if args.dump_json:
        print()
        print(json.dumps(od, ensure_ascii=False, indent=2))
    return 0


def cmd_bus_stats(args, out):
    """--bus-stats：接口现状与统计（只读）。"""
    cfg, cfg_path = load_device_json()
    item, _ = find_can_bus(cfg, LOGICAL_BUS)
    iface = item.get("iface", "")
    out.title("总线状态 {}（{}）".format(LOGICAL_BUS, iface or "未配置"))
    out.info("只读子命令：不发帧，不要求 --i-know-rt-is-running。")
    out.field("配置文件", cfg_path)

    if not iface:
        out.bad("配置里 can-2 的 iface 为空。")
        return 2

    actual = read_iface_actual(iface)
    if actual is None:
        out.warn("接口 {} 不存在。".format(iface))
        out.info("可能是 {} 未运行，或 SPI-CAN 驱动未加载。".format(CAN_SERVICE))
        out.info("驱动模块名以设备树 compatible 为准，尚未实机确认填入。")
        return 1
    if actual.get("error"):
        out.warn(actual["error"])
        return 1

    out.field("state", actual.get("state"))
    out.field("flags", actual.get("flags"))
    cur = actual.get("bitrate")
    out.field("bitrate", "{} bps".format(cur) if cur is not None else "(未设置)")
    declared = item.get("bitrate")
    out.field("配置声明", "{} bps".format(declared) if declared else "(缺失)")
    if declared and cur is not None and cur != declared:
        out.warn("位速率与配置不一致：现状 {} != 配置 {}".format(cur, declared))
        out.info("配置是 /etc/km3/device.json；运行时生效值来自 {} 的 CAN_BITRATE=".format(
            CAN_ENV))

    stats = parse_ip_stats(iface)
    if "error" in stats:
        out.warn(stats["error"])
    else:
        print()
        for k in ("rx_packets", "rx_errors", "rx_dropped",
                  "tx_packets", "tx_errors", "tx_dropped"):
            if k in stats:
                out.field(k, stats[k])
        can_specific = stats.get("can_specific")
        if can_specific:
            for k, v in sorted(can_specific.items()):
                out.field("can/" + k, v)
        else:
            out.warn("未取到 CAN 专用错误计数（bus-off 次数等）。")
            out.info("这些计数**不在 sysfs**，只能从 netlink 的 IFLA_INFO_XSTATS 取。")
            out.info("本工具当前没有实现 netlink 读取，因此不拿通用 rx_errors 冒充。")

    if args.show_raw:
        print()
        print(stats.get("raw", ""))
    return 0


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
    try:
        while True:
            if args.duration and (time.monotonic() - start) >= args.duration:
                break
            f = bus.recv(timeout=0.5)
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
    """--test：不带硬件的自检。

    测的三件事都是"工具自己靠不靠得住"，不是"电机好不好"：
      1. 配置可读，且能解析出 can-2；
      2. 对象字典声明确实全部未验证（驱动守卫的前提）；
      3. 接口能被打开并绑定（km3 用户能开 AF_CAN/SOCK_RAW 是实测事实，
         值得每次验证 —— 它一旦不成立，所有发帧类子命令都会失败）。
    """
    out.title("自检")
    failures = 0

    try:
        cfg, cfg_path = load_device_json()
        out.ok("设备配置可读：{}".format(cfg_path))
    except ConfigError as exc:
        out.bad(str(exc))
        return 1

    item = None
    try:
        item, array_name = find_can_bus(cfg, LOGICAL_BUS)
        out.ok("找到逻辑总线 {}（bus.{}，iface={}）".format(
            LOGICAL_BUS, array_name, item.get("iface") or "(空)"))
    except ConfigError as exc:
        out.bad(str(exc))
        failures += 1

    out.ok("python3 {}.{}".format(sys.version_info.major, sys.version_info.minor))

    try:
        import curses  # noqa: F401
        out.ok("curses 可导入")
    except ImportError:
        out.warn("curses 不可导入；TUI 会退化为纯文本输出（等同 --no-tui）")

    if std_layer_available():
        out.ok("标准层 tools/hwtui 可用（canbus / canopen）")
        out.info("CANopen 帧组装与状态机来自标准层，本工具不重复实现。")
    else:
        out.warn("标准层 tools/hwtui 不可用：{}".format(_HWTUI_ERROR))
        out.info("发帧路径会退化到内置实现（只做 SocketCAN 收发）。")

    for tool in ("ip", "systemctl"):
        found = shutil.which(tool)
        if found:
            out.ok("{} 可用：{}".format(tool, found))
        else:
            out.warn("{} 不在 PATH 上".format(tool))

    # 对象字典守卫：od.json 全部条目必须是 unverified。
    od_path = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "driver", "od.json"))
    if os.path.exists(od_path):
        with open(od_path, "r", encoding="utf-8") as fh:
            od = json.load(fh)
        entries = od.get("entries", [])
        bad = [e.get("key") for e in entries
               if e.get("status") != "unverified" or e.get("cite") is not None]
        if bad:
            out.bad("od.json 里有条目已标记为已验证：{}".format(", ".join(bad)))
            out.info("如果没有对应的厂家资料，这就是一条没有依据的索引。")
            failures += 1
        else:
            out.ok("od.json 的 {} 条全部 status=unverified、cite=null".format(len(entries)))
    else:
        out.warn("找不到 {}（板端只部署工具时属正常）".format(od_path))

    # 实测事实：km3 用户能开 AF_CAN/SOCK_RAW 并绑定接口。值得每次验证，
    # 因为它一旦不成立，所有发帧类子命令都会失败，而报错信息不会解释原因。
    if item and item.get("iface"):
        try:
            bus = connect_bus(item["iface"], out)
            bus.close()
            out.ok("可以打开并绑定 {}".format(item["iface"]))
        except ConfigError as exc:
            out.warn("打开 {} 失败：{}".format(item["iface"], exc))
    else:
        out.warn("配置里 iface 为空，跳过套接字自检")

    print()
    if failures:
        out.bad("自检未通过（{} 项）".format(failures))
        return 1
    out.ok("自检通过")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="pmp4010.py",
        description="PMP4010（机身抬降电机 A）板载调试 TUI。只读子命令任何身份可跑；"
                    "发帧类子命令（--init 写文件、--nmt 发帧）在 km3-rt-controller "
                    "active 时要求显式确认。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python3 pmp4010.py --init --verify        # 纯只读，看现状与差异\n"
               "  python3 pmp4010.py --init                 # 写 CAN_BITRATE 并打印 root 命令\n"
               "  python3 pmp4010.py --bus-stats            # 接口现状与统计\n"
               "  python3 pmp4010.py --od-dump              # 打印对象字典声明（全未验证）\n"
               "  python3 pmp4010.py --motion stop --node 1 # 手工点动（当前始终拒绝）\n"
               "  python3 pmp4010.py --raw --count 20       # 只监听，抓 20 帧\n"
               "  python3 pmp4010.py --scan --listen-ms 2000\n"
               "  python3 pmp4010.py --nmt --nmt-command start --node 1 --i-know-rt-is-running\n")

    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--init", action="store_true",
                        help="三段式初始化：读配置与现状、写 {}，打印 root 命令".format(CAN_ENV))
    action.add_argument("--test", action="store_true",
                        help="自检：配置可读、curses 可用、对象字典守卫有效")
    action.add_argument("--scan", action="store_true",
                        help="被动监听总线，看哪些节点在说话（只读）")
    action.add_argument("--nmt", action="store_true", help="发送 NMT 命令（发帧）")
    action.add_argument("--od-dump", action="store_true", help="打印对象字典声明（只读）")
    action.add_argument("--motion", choices=["enable", "disable", "stop", "home"],
                        help="手工点动（发帧）。当前因对象字典未验证而始终拒绝执行")
    action.add_argument("--bus-stats", action="store_true", help="接口现状与统计（只读）")
    action.add_argument("--raw", action="store_true", help="只监听原始帧（只读）")

    p.add_argument("--verify", action="store_true",
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
                   help="--scan 关注的节点号范围，如 1-4。默认不限（显示所有节点）")
    p.add_argument("--listen-ms", type=int, default=2000,
                   help="--scan 的监听时长（毫秒，默认 2000）")
    p.add_argument("--count", type=int, default=0,
                   help="--raw 收够多少帧后退出（0 = 不限）")
    p.add_argument("--duration", type=int, default=0,
                   help="--raw 持续多少秒后退出（0 = 不限）")
    p.add_argument("--show-raw", action="store_true",
                   help="--bus-stats 时额外打印 ip 命令的原始输出")
    p.add_argument("--dump-json", action="store_true",
                   help="--od-dump 时额外打印完整 JSON")
    return p


def main(argv=None):
    # 行缓冲（开发规范 §1.5 的同一条规矩）：被 tee / journal 捕获时，
    # 十几秒的 --raw 若一直没输出，现场只能靠猜工具是不是卡了。
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    parser = build_parser()
    args = parser.parse_args(argv)
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
    except ConfigError as exc:
        out.bad(str(exc))
        return 3
    except KeyboardInterrupt:
        out.info("中断。")
        return 130
    except ValueError as exc:
        out.bad(str(exc))
        return 2

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
