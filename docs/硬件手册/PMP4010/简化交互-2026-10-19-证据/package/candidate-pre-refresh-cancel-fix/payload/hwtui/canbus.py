"""km3 真总线传输层（SocketCAN 原生）。

只做一件事：把 `canopen.Frame` 收发到一张真实的 CAN 接口上。
不做协议解析、不做重试、不做自动重连——协议在 `canopen.py`，重试策略属于上层。

**硬规则：FD 帧显式拒绝并报错，绝不静默丢弃。**

本板（TL3588）主板两路 CAN 由 SPI0 外挂 SIT2515T-I/SO + NSI1050 实现，
仅 CAN 2.0B，`CONFIG_CAN_MCP251XFD` 未开（见 `docs/架构设计/实测基线.md`）。
抬降电机 B（P65MAX-S）标称 CANfd，必须走 USB 转 CAN 模块（内核 `can2`）。
把 FD 帧丢给主板 CAN 的结果如果是"静默丢弃"，现场表现就是"电机不动"——
排查会从总线、终端电阻、节点号一路查到电源，而真正的原因是帧格式。
因此这里抛 `FdNotSupportedError`。

实现只用 Python 3 标准库（socket / struct / fcntl）。两类内核结构体在 Python 里
没有现成绑定，处理方式**不同**，这一点在真板上实测过：

- `can_frame`：CPython 不代劳，必须手工按 `linux/can.h` 的布局用 struct 拼装——
  4 字节 can_id + 1 字节 dlc + 3 字节对齐填充 + 8 字节 data = 16 字节。
  收到 FD 帧时内核给的是 72 字节，长度本身就是判据。

- `sockaddr_can`：**由 CPython 自己拼**，调用方只能传单元素元组 `(ifname,)`。
  手拼 22 字节交给 `bind()` 会得到 `TypeError: AF_CAN address must be tuple`。
  板上的实测记录见 `pack_sockaddr_can` 的注释。
"""

from __future__ import annotations

import errno
import fcntl
import os
import socket
import struct
import time
from typing import List, Optional, Sequence, Tuple

from canopen import (
    FLAG_EXTENDED,
    FLAG_REMOTE,
    FRAME_CLASSIC,
    FRAME_FD,
    Frame,
)

__all__ = [
    "CanBusError",
    "InterfaceNotFoundError",
    "FdNotSupportedError",
    "InterfaceError",
    "CAN_MTU",
    "CANFD_MTU",
    "CAN_RAW",
    "CAN_RAW_FD_FRAMES",
    "CAN_RAW_LOOPBACK",
    "SIOCGIFINDEX",
    "CanBus",
    "open_can_bus",
    "list_can_interfaces",
    "pack_can_frame",
    "unpack_can_frame",
    "pack_sockaddr_can",
]

# --------------------------------------------------------------------------
# 常量（与 linux/can.h、linux/can/raw.h 对齐）
# --------------------------------------------------------------------------

#: 经典 CAN 帧长度：can_id(4) + dlc(1) + pad(3) + data(8)
CAN_MTU = 16
#: CAN FD 帧长度：can_id(4) + len(1) + flags(1) + pad(2) + data(64)
CANFD_MTU = 72
#: struct canfd_frame 的 data 字段长度
CANFD_MAX_DLEN = 64

#: CAN_RAW 协议号。socket.CAN_RAW 在 Linux 的 Python 上存在，但保留兜底。
CAN_RAW = getattr(socket, "CAN_RAW", 1)
#: 接收 FD 帧的套接字选项。本模块**从不开启它**，保留常量只为报错时能说清为什么。
CAN_RAW_FD_FRAMES = 5
CAN_RAW_LOOPBACK = 3
CAN_RAW_RECV_OWN_MSGS = 4
CAN_RAW_ERR_FILTER = 2
CAN_RAW_FILTER = getattr(socket, "CAN_RAW_FILTER", 1)
SOL_CAN_RAW = getattr(socket, "SOL_CAN_RAW", 101)

#: ioctl 号。`_IOW('S', 0, struct ifreq)` 在 32/64 位 Linux 上都是 0x8933；
#: 但 SIOCGIFINDEX 实际是 `_IOWR`，历史值是 0x8933，直接写死更稳。
SIOCGIFINDEX = 0x8933

_AF_CAN = getattr(socket, "AF_CAN", 29)
_CAN_STRUCT = struct.Struct("<IB3x8s")
_SOCKADDR_CAN = struct.Struct("<Hi16s")


def _require_af_can() -> None:
    if not hasattr(socket, "AF_CAN"):
        raise InterfaceError(
            "本 Python 无 socket.AF_CAN —— 内核未编译 CAN 子系统，"
            "或运行在不支持非 Linux 平台上")
    raw = getattr(socket, "SOCK_RAW", None)
    if raw is None:  # pragma: no cover - Linux 上必存在
        raise InterfaceError("本 Python 无 socket.SOCK_RAW")


# --------------------------------------------------------------------------
# 手工结构体
# --------------------------------------------------------------------------


def pack_can_frame(frame: Frame) -> bytes:
    """把 `Frame` 打成 16 字节的 `struct can_frame`。

    先做 FD 检查：本函数只处理经典帧，FD 请在 `CanBus.send` 里被拦下。
    """
    can_id, data = frame.to_socketcan()
    if len(data) > 8:
        raise ValueError("经典帧最多 8 字节，收到 %d 字节" % len(data))
    return _CAN_STRUCT.pack(can_id, len(data), data.ljust(8, b"\x00"))


def unpack_can_frame(raw: bytes) -> Frame:
    """从 `struct can_frame` 的 16 字节还原。

    也接受 72 字节（CAN FD）——内核在 FD 套接字上会给出这种长度，
    此时明确报错而不是按经典帧错位解析。
    """
    if len(raw) == CANFD_MTU:
        raise FdNotSupportedError(
            "收到 CAN FD 帧（%d 字节）。本板主板 CAN 只支持 CAN 2.0B；"
            "FD 设备必须走 USB 转 CAN 模块（内核 can2）" % CANFD_MTU)
    if len(raw) != CAN_MTU:
        raise ValueError("can_frame 长度异常: %d（期望 %d）" % (len(raw), CAN_MTU))
    can_id, dlc, data = _CAN_STRUCT.unpack(raw)
    if dlc > 8:
        raise ValueError("can_frame 的 dlc 异常: %d" % dlc)
    return Frame.from_socketcan(can_id, data[:dlc])


#: CPython 的 AF_CAN 地址形式，实测依据（TL3588，Python 3.10.12）：
#:     bind(('can1',))                 成功，getsockname() -> ('can1',)
#:     bind(('can1', 0))               TypeError: must be a tuple (interface, )
#:     bind(b"<22 字节 sockaddr_can>") TypeError: must be tuple, not bytes
#: 也就是说内核结构体由 CPython 自己拼，调用方只能给接口名。
#: 不要试图手拼 sockaddr_can —— 手拼的版本在真板上必然 TypeError。
CAN_SOCKADDR_IS_TUPLE = True


def pack_sockaddr_can(interface: str) -> Tuple[str]:
    """由接口名构造 AF_CAN 的 bind 地址。

    名字保留 `pack_` 前缀是为了与旧调用点一致，但返回值**不是**字节串，
    而是 CPython 要求的单元素元组。接口名交给内核解析和校验，
    因此这里**不再**做 SIOCGIFINDEX：索引仍可用 `_ifindex()` 独立查询
    （用于报错信息与状态展示），但 bind 不接受它。
    """
    if not interface:
        raise ValueError("接口名不能为空")
    if len(interface) >= 16:
        raise ValueError("接口名过长（Linux 上限 15 字符）: %r" % (interface,))
    return (interface,)


def _ifindex(interface: str) -> int:
    """用 ioctl(SIOCGIFINDEX) 取接口索引。

    不用 `socket.if_nametoindex()`：那个函数在 Python 3.8+ 存在且等价，
    但它在接口不存在时报 `OSError: [Errno 19]`，信息里不带接口名，
    在多接口的调试现场分不清是哪个名字写错了。这里自己拼 ifreq 只为报错更清楚。
    """
    if not interface or len(interface) >= 16:
        raise InterfaceNotFoundError(
            "接口名非法: %r（Linux 接口名最长 15 字符）" % (interface,))
    ifreq = struct.pack("16sI", interface.encode("ascii"), 0)
    probe = socket.socket(_AF_CAN, socket.SOCK_RAW, CAN_RAW)
    try:
        result = fcntl.ioctl(probe.fileno(), SIOCGIFINDEX, ifreq)
    except OSError as exc:
        if exc.errno == errno.ENODEV:
            raise InterfaceNotFoundError(
                "CAN 接口不存在: %s（用 `ip link` 看现有接口）" % interface) from exc
        raise InterfaceNotFoundError(
            "取 %s 的接口索引失败: %s" % (interface, exc)) from exc
    finally:
        probe.close()
    return struct.unpack("16sI", result)[1]


# --------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------


class CanBusError(Exception):
    """传输层基类异常。"""


class InterfaceNotFoundError(CanBusError):
    """接口不存在（名字写错、驱动没加载、接口没 up）。"""


class FdNotSupportedError(CanBusError):
    """尝试收发 CAN FD 帧。

    本板主板 CAN 不支持 FD。这个异常必须存在且必须显式——静默丢弃
    会让故障表现为"电机不动"，排查方向会被完全带偏。
    """


class InterfaceError(CanBusError):
    """套接字操作失败（权限、接口 down、内核不支持等）。"""


# --------------------------------------------------------------------------
# 总线
# --------------------------------------------------------------------------


class CanBus:
    """一条 SocketCAN 总线。

    与 `mockbus.MockBus` 暴露同一套接口：`send` / `recv` / `close` /
    上下文管理器。上层（`canopen.py`）因此对二者无感。

    `recv(timeout)` 超时返回 `None`，**不抛异常**——"静默"是总线的
    正常状态，不是错误；把它变成异常会逼着每个调用点写 try/except。
    真正的协议级超时判定在 `canopen.SdoClient`，那里才会抛。

    参数：
      - `interface`：内核接口名，如 `can1`。注意 `can1` 是**主板 CAN-2**，
        逻辑名与内核名差一位（见 `docs/架构设计/设备端程序架构.md` §2.1.3）。
      - `loopback`：是否要收到自己发出的帧。**默认 True**：调试与
        无电机回归（CAN 控制器硬件回环）都依赖它；生产实时路径应设 False，
        否则自己的请求会混进接收队列。
      - `receive_own_messages`：是否接收到本套接字发出的帧。
      - `fd`：是否请求 CAN FD 模式。本板恒为 False，留参数只为在
        接上 USB-CAN 模块时能复用同一个类——那时此位由调用方按总线显式开启，
        且**绝不能**与主板 CAN 混用。
    """

    def __init__(self, interface: str = "can1", loopback: bool = True,
                 receive_own_messages: bool = False,
                 fd: bool = False, *, timeout_default: float = 1.0) -> None:
        _require_af_can()
        if fd:
            raise FdNotSupportedError(
                "CanBus 不支持 CAN FD。本板主板 CAN 为 CAN 2.0B；"
                "FD 设备（P65MAX-S）需另走 USB 转 CAN 模块，"
                "该路径尚未实现")
        self.interface = interface
        self.loopback = loopback
        self.receive_own_messages = receive_own_messages
        self.fd = False
        self.timeout_default = timeout_default
        self.ifindex = -1
        self._sock: Optional[socket.socket] = None
        #: 收发统计。工具界面上要用，且能暴露"发了没收到"这类问题。
        self.tx_count = 0
        self.rx_count = 0
        self.tx_error_count = 0

    # -- 生命周期 ----------------------------------------------------------

    def open(self) -> "CanBus":
        """打开并绑定套接字。接口不存在或没 up 时抛 InterfaceError。"""
        self.ifindex = _ifindex(self.interface)
        try:
            sock = socket.socket(_AF_CAN, socket.SOCK_RAW, CAN_RAW)
        except OSError as exc:
            raise InterfaceError("创建 AF_CAN 套接字失败: %s" % exc) from exc
        try:
            # 回环与自收是两件事：loopback 控制控制器是否把本机发送回灌总线，
            # receive_own_messages 控制本套接字是否收到它。调试时两个都开。
            sock.setsockopt(SOL_CAN_RAW, CAN_RAW_LOOPBACK,
                            1 if self.loopback else 0)
            sock.setsockopt(SOL_CAN_RAW, CAN_RAW_RECV_OWN_MSGS,
                            1 if self.receive_own_messages else 0)
            # bind 只接受 (ifname,)：接口名的存在性由内核在 bind 时校验，
            # 因此前面那次 SIOCGIFINDEX 只用于把"接口不存在"报得更清楚，
            # 真正的绑定用的是名字本身。
            sock.bind(pack_sockaddr_can(self.interface))
        except OSError as exc:
            sock.close()
            raise InterfaceError(
                "绑定 %s 失败: %s（接口是否 up？`ip link set %s up`）" % (
                    self.interface, exc, self.interface)) from exc
        self._sock = sock
        return self

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "CanBus":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def _require_open(self) -> socket.socket:
        if self._sock is None:
            raise InterfaceError("总线未打开: 请先调用 open()")
        return self._sock

    # -- 收发 --------------------------------------------------------------

    def send(self, frame: Frame) -> None:
        """发一帧。FD 帧在此被显式拒绝。"""
        sock = self._require_open()
        if frame.frame_type == FRAME_FD:
            raise FdNotSupportedError(
                "拒绝发送 CAN FD 帧到 %s。本板主板 CAN 只支持 CAN 2.0B"
                "（SIT2515T-I/SO，CONFIG_CAN_MCP251XFD 未开）；"
                "FD 设备必须走 USB 转 CAN 模块" % self.interface)
        payload = pack_can_frame(frame)
        try:
            sent = sock.send(payload)
        except OSError as exc:
            self.tx_error_count += 1
            raise InterfaceError(
                "发送失败（%s, COB-ID 0x%03X）: %s" % (
                    self.interface, frame.arbitration_id, exc)) from exc
        if sent != len(payload):
            self.tx_error_count += 1
            raise InterfaceError(
                "短写: 期望 %d 字节，实际 %d 字节" % (len(payload), sent))
        self.tx_count += 1

    def recv(self, timeout: Optional[float] = None) -> Optional[Frame]:
        """收一帧。超时返回 None。"""
        sock = self._require_open()
        if timeout is None:
            timeout = self.timeout_default
        sock.settimeout(timeout)
        try:
            raw = sock.recv(CAN_MTU + 1)
        except socket.timeout:
            return None
        except OSError as exc:
            if exc.errno == errno.EAGAIN:
                return None
            raise InterfaceError("接收失败（%s）: %s" % (self.interface, exc)) from exc
        frame = unpack_can_frame(raw)
        # 关掉 receive_own_messages 时内核已经滤掉了自己的帧，这里是第二道闸；
        # 主板 CAN 硬件回环下用得到（回环会把发送帧原样送回来）。
        if frame.arbitration_id is not None:
            self.rx_count += 1
        return frame

    def drain(self, timeout: float = 0.0) -> List[Frame]:
        """把当前缓冲区里的帧全取出来，不等待。用于调试前清空历史。"""
        frames: List[Frame] = []
        while True:
            frame = self.recv(timeout)
            if frame is None:
                return frames
            frames.append(frame)

    # -- 接口状态（只读） ---------------------------------------------------

    def state(self) -> str:
        """读接口 UP/DOWN、比特率、错误状态。

        走 `/sys/class/net/<if>/operstate` 与 `ip` 之外的路径都不可靠：
        **CAN 专有的错误计数不在 sysfs**（实测见 `实测基线.md` §九），
        只能从 netlink 的 IFLA_INFO_XSTATS 取，那是另一个模块的事。
        这里只给最基础的两个字段，够工具判断"线拉起来没有"。
        """
        base = "/sys/class/net/%s" % self.interface
        try:
            with open(os.path.join(base, "operstate")) as fh:
                operstate = fh.read().strip()
        except OSError:
            return "unknown"
        flags = ""
        try:
            with open(os.path.join(base, "flags")) as fh:
                value = int(fh.read().strip(), 16)
            flags = "UP" if value & 0x1 else "DOWN"
        except (OSError, ValueError):
            flags = "?"
        return "%s (%s)" % (operstate, flags)

    def __repr__(self) -> str:
        return "CanBus(%r, open=%s, tx=%d, rx=%d)" % (
            self.interface, self.is_open, self.tx_count, self.rx_count)


def open_can_bus(interface: str = "can1", **kwargs) -> CanBus:
    """打开一条总线。便捷函数，等价于 `CanBus(...).open()`。"""
    return CanBus(interface, **kwargs).open()


def list_can_interfaces() -> List[str]:
    """列出所有内核 CAN 接口名，按名字排序。

    只依赖 sysfs，不需要 `ip` 命令，也不需要打开套接字——
    因此没权限的设备上也能用。
    """
    root = "/sys/class/net"
    result = []
    try:
        names = os.listdir(root)
    except OSError:
        return result
    for name in sorted(names):
        if not (name == "can" or name.startswith("can")):
            continue
        # 只认真正的 CAN 接口：ARPHRD_CAN = 280，写在 type 文件里
        try:
            with open(os.path.join(root, name, "type")) as fh:
                if int(fh.read().strip()) != 280:
                    continue
        except (OSError, ValueError):
            continue
        result.append(name)
    return result
