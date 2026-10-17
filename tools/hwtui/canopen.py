"""km3 CANopen 标准层。

本模块只实现 **CiA 301 公开规范**的部分：SDO、NMT、心跳、EMCY、PDO 映射的只读访问。
分层理由与硬规则（违反即视为缺陷）：

1. **不得把任何厂家特定的对象字典索引硬编码进本文件。**
   厂家索引属于 `od.json`，由调用方按需读入。本文件只认规范里的公共对象
   （设备型号 0x1000、厂家名称 0x1008、软件版本 0x100A、标识 0x1018 等），
   以及规范固定含义的通讯参数区（0x1400+ / 0x1600+ / 0x1800+ / 0x1A00+）。
   原因：PMP4010 的对象字典尚未到手（见 `docs/硬件手册/PMP4010/README.md`），
   此刻写进代码的任何厂家索引都是猜测，而猜测会被后续代码当成事实继承。

2. **只用 Python 3 标准库。** 该层要在 TL3588 板端（python3 3.10.12）直接跑。

3. **未知的 abort code 原样显示十六进制，不编造含义。**
   规范内未定义的值一律标注 "unknown"，把判断留给现场。

帧模型与传输层解耦：本模块只依赖 `bus` 对象上的 `send(frame)` / `recv(timeout)`，
接口形状见 `canbus.py`（真总线）与 `mockbus.py`（模拟总线），二者可互换。
"""

from __future__ import annotations

import struct
from functools import wraps
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "Frame",
    "FRAME_CLASSIC",
    "FRAME_FD",
    "FLAG_EXTENDED",
    "FLAG_REMOTE",
    "cob_id_sdo_tx",
    "cob_id_sdo_rx",
    "cob_id_heartbeat",
    "cob_id_emcy",
    "COB_ID_NMT_BROADCAST",
    "cob_id_pdo_tx",
    "cob_id_pdo_rx",
    "SDO_CS_DOWNLOAD_SEGMENTED",
    "SDO_CS_DOWNLOAD_EXPEDITED",
    "SDO_CS_DOWNLOAD_SIZE_INDICATED",
    "SDO_CS_DOWNLOAD_EXPEDITED_1",
    "SDO_CS_DOWNLOAD_EXPEDITED_2",
    "SDO_CS_DOWNLOAD_EXPEDITED_3",
    "SDO_DOWNLOAD_SIZE_MASK",
    "SDO_DOWNLOAD_CS_MASK",
    "SDO_CS_UPLOAD_REQUEST",
    "SDO_CS_UPLOAD_SEGMENTED",
    "SDO_CS_UPLOAD_SEGMENTED_NO_SIZE",
    "SDO_CS_UPLOAD_SEGMENT_RESPONSE",
    "SDO_CS_UPLOAD_SEGMENTED_TOGGLE1",
    "SDO_CS_UPLOAD_SEGMENT_MASK",
    "SDO_CS_UPLOAD_EXPEDITED",
    "SDO_CS_UPLOAD_EXPEDITED_1",
    "SDO_CS_UPLOAD_EXPEDITED_2",
    "SDO_CS_UPLOAD_EXPEDITED_3",
    "SDO_CS_UPLOAD_EXPEDITED_BASE",
    "SDO_CS_ABORT",
    "SDO_CS_UPLOAD_SEGMENT_REQUEST",
    "SDO_UPLOAD_SEGMENTED_WITH_SIZE",
    "SDO_CS_DOWNLOAD_SEGMENT_ACK",
    "SDO_CS_DOWNLOAD_INITIATE_ACK",
    "SDO_INDEX_TABLE",
    "ABORT_CODES",
    "CanopenError",
    "SdoAbort",
    "BusSilenceError",
    "SdoProtocolError",
    "SdoSessionQuarantined",
    "NmtState",
    "NMT_COMMANDS",
    "Emcy",
    "HeartbeatEvent",
    "HeartbeatMonitor",
    "SdoClient",
    "NmtMaster",
    "scan_nodes",
    "PdoMapping",
    "PdoCommunication",
    "read_pdo_mapping",
    "read_pdo_communication",
    "describe_object",
    "decode_abort_code",
    "format_object",
]


# --------------------------------------------------------------------------
# 帧模型
# --------------------------------------------------------------------------

#: 帧类型标识（不是 CAN 帧里的位，是本地枚举）。
FRAME_CLASSIC = "classic"
FRAME_FD = "fd"

#: SocketCAN 里两位高位标志。仲裁 ID 本体只占低 29 位（扩展帧）/ 低 11 位（标准帧）。
FLAG_EXTENDED = 0x80000000
FLAG_REMOTE = 0x40000000

#: 标准帧 11 位 ID 的上限。
_STANDARD_ID_MAX = 0x7FF


@dataclass(frozen=True)
class Frame:
    """一个 CAN 帧。

    `arbitration_id` 只放仲裁 ID 本体（CANopen 里就是 COB-ID），**不含**标志位；
    扩展/RTR 由 `extended` / `remote` 显式表达。这样 COB-ID 的算术
    （0x600 + node_id 之类）不受位运算污染——把标志位混进 COB-ID 再比较，
    是这类工具最常见的错法。

    `frame_type` 默认 `FRAME_CLASSIC`。FD 帧由 `canbus.py` 显式拒绝并报错。
    """

    arbitration_id: int
    data: bytes = b""
    extended: bool = False
    remote: bool = False
    frame_type: str = FRAME_CLASSIC

    def __post_init__(self) -> None:
        if not 0 <= self.arbitration_id <= (
                0x1FFFFFFF if self.extended else _STANDARD_ID_MAX):
            raise ValueError(
                "仲裁 ID 超范围: 0x%X (extended=%s)" % (self.arbitration_id, self.extended))
        if self.frame_type not in (FRAME_CLASSIC, FRAME_FD):
            raise ValueError("未知帧类型: %r" % (self.frame_type,))
        if self.frame_type == FRAME_FD and len(self.data) > 64:
            raise ValueError("FD 帧数据过长: %d" % len(self.data))
        if self.frame_type == FRAME_CLASSIC and len(self.data) > 8:
            raise ValueError("经典帧数据过长: %d" % len(self.data))
        object.__setattr__(self, "data", bytes(self.data))

    @property
    def dlc(self) -> int:
        return len(self.data)

    def to_socketcan(self) -> Tuple[int, bytes]:
        """转成 `(can_id, data)`，`can_id` 带上标志位，可直接喂 struct。"""
        can_id = self.arbitration_id
        if self.extended:
            can_id |= FLAG_EXTENDED
        if self.remote:
            can_id |= FLAG_REMOTE
        return can_id, self.data

    @classmethod
    def from_socketcan(cls, can_id: int, data: bytes) -> "Frame":
        """从 SocketCAN 的 `(can_id, data)` 还原。"""
        return cls(
            arbitration_id=can_id & 0x1FFFFFFF,
            data=data,
            extended=bool(can_id & FLAG_EXTENDED),
            remote=bool(can_id & FLAG_REMOTE),
        )

    def __str__(self) -> str:
        hexdata = " ".join("%02X" % b for b in self.data)
        return "%03X  [%d]  %s" % (self.arbitration_id, len(self.data), hexdata)


# --------------------------------------------------------------------------
# COB-ID 计算（CiA 301 §7.2.1 预定义连接集）
# --------------------------------------------------------------------------
#
# 这些是规范给死的偏移，与厂家无关。节点号 1..127（0 是 NMT 主站自身）。


def _check_node_id(node_id: int) -> int:
    if not 0 <= node_id <= 0x7F:
        raise ValueError("节点号超范围（0..127）: %r" % (node_id,))
    return node_id


def cob_id_sdo_tx(node_id: int) -> int:
    """SDO 请求（主站 -> 从站），客户端发，服务端收：0x600 + node_id。"""
    return COB_SDO_REQUEST + _check_node_id(node_id)


def cob_id_sdo_rx(node_id: int) -> int:
    """SDO 应答（从站 -> 主站）：0x580 + node_id。"""
    return COB_SDO_RESPONSE + _check_node_id(node_id)


def cob_id_heartbeat(node_id: int) -> int:
    """心跳 / 节点保护：0x700 + node_id。"""
    return 0x700 + _check_node_id(node_id)


def cob_id_emcy(node_id: int) -> int:
    """紧急报文 EMCY：0x080 + node_id。注意它比 SDO 应答的基址更靠前。"""
    return 0x080 + _check_node_id(node_id)


#: NMT 广播 COB-ID。NMT 只有主站发、全体收，没有节点号。
COB_ID_NMT_BROADCAST = 0x000


def cob_id_pdo_tx(node_id: int, pdo_number: int) -> int:
    """预定义连接的 TPDO COB-ID 默认值（CiA 301 默认分配）。

    pdo_number 从 1 起。1..4 用 0x180+ / 0x280+ / 0x380+ / 0x480+，
    5..8 起改用扩展帧的 0x180 基址（这里不猜扩展形态，直接报错要求查 EDS）。
    实际 COB-ID 应以对象 0x1800 的读数为准——本函数只给规范默认值。
    """
    base = {1: 0x180, 2: 0x280, 3: 0x380, 4: 0x480}.get(pdo_number)
    if base is None:
        raise ValueError("PDO 号 %r 无规范默认 COB-ID，请读 0x1800 对象" % (pdo_number,))
    return base + _check_node_id(node_id)


def cob_id_pdo_rx(node_id: int, pdo_number: int) -> int:
    """预定义连接的 RPDO COB-ID 默认值：1..4 为 0x200/0x300/0x400/0x500 + node。"""
    base = {1: 0x200, 2: 0x300, 3: 0x400, 4: 0x500}.get(pdo_number)
    if base is None:
        raise ValueError("PDO 号 %r 无规范默认 COB-ID，请读 0x1400 对象" % (pdo_number,))
    return base + _check_node_id(node_id)


# --------------------------------------------------------------------------
# SDO 命令字（CiA 301 §7.2.4）
# --------------------------------------------------------------------------

# The generated module contains only shared wire constants. Support deployment's
# namespace-package import and the tools' standalone imports without changing sys.path.
if __package__:
    from .canopen_wire import *
else:
    from canopen_wire import *


#: 规范里**含义固定**的公共对象索引。厂家私有对象**不在**此处——它们属于 od.json。
SDO_INDEX_TABLE: Dict[int, str] = {
    0x1000: "device type",
    0x1001: "error register",
    0x1003: "pre-defined error field",
    0x1005: "COB-ID SYNC message",
    0x1006: "communication cycle period",
    0x1008: "manufacturer device name",
    0x1009: "manufacturer hardware version",
    0x100A: "manufacturer software version",
    0x1010: "store parameters",
    0x1011: "restore default parameters",
    0x1014: "COB-ID EMCY",
    0x1015: "inhibit time EMCY",
    0x1016: "consumer heartbeat time",
    0x1017: "producer heartbeat time",
    0x1018: "identity object",
    0x1029: "error behavior",
}


def describe_object(index: int) -> str:
    """给出公共对象的规范名；未知索引原样返回十六进制，不编造。"""
    name = SDO_INDEX_TABLE.get(index)
    if name is None:
        return "unknown object 0x%04X" % index
    return name


def format_object(index: int, subindex: int) -> str:
    return "0x%04X:%02X (%s)" % (index, subindex, describe_object(index))


# --------------------------------------------------------------------------
# 错误与 abort code
# --------------------------------------------------------------------------

#: CiA 301 表 41 的 SDO abort code。**只收规范定义的值。**
#: 查到未知码时 `decode_abort_code` 给 "unknown"，绝不猜厂家含义。
ABORT_CODES: Dict[int, str] = {
    0x05030000: "toggle bit not alternated",
    0x05040000: "SDO protocol timed out",
    0x05040001: "client/server command specifier not valid or unknown",
    0x05040002: "invalid block size",
    0x05040003: "invalid sequence number",
    0x05040004: "CRC error",
    0x05040005: "out of memory",
    0x06010000: "unsupported access to an object",
    0x06010001: "attempt to read a write only object",
    0x06010002: "attempt to write a read only object",
    0x06020000: "object does not exist in the object dictionary",
    0x06040041: "object cannot be mapped to the PDO",
    0x06040042: "the number and length of the objects to be mapped "
                "would exceed PDO length",
    0x06040043: "general parameter incompatibility reason",
    0x06040047: "general internal incompatibility in the device",
    0x06060000: "access failed due to a hardware error",
    0x06070010: "data type does not match, length of service parameter does not match",
    0x06070012: "data type does not match, length of service parameter too high",
    0x06070013: "data type does not match, length of service parameter too low",
    0x06090011: "sub-index does not exist",
    0x06090030: "value range of parameter exceeded (only for write access)",
    0x06090031: "value of parameter written too high",
    0x06090032: "value of parameter written too low",
    0x06090036: "maximum value is less than minimum value",
    0x08000000: "general error",
    0x08000020: "data cannot be transferred or stored to the application",
    0x08000021: "data cannot be transferred or stored to the application "
                "because of local control",
    0x08000022: "data cannot be transferred or stored to the application "
                "because of the present device state",
    0x08000023: "object dictionary dynamic generation fails or no object "
                "dictionary is present",
}


def decode_abort_code(code: int) -> str:
    """把 abort code 翻成规范文字。

    未知码返回 `"unknown abort code 0x........"` —— 原样十六进制。
    十六进制本身就是现场能拿去查手册的东西；替它编一个"像是原因"的字符串，
    会把一次排障变成一次误导。
    """
    name = ABORT_CODES.get(code)
    if name is None:
        return "unknown abort code 0x%08X" % code
    return name


class CanopenError(Exception):
    """CANopen 层的基类异常。"""


class BusSilenceError(CanopenError):
    """在超时窗口内没有收到任何应答帧。

    与 SdoAbort 是两回事：abort 是节点回了话并说"不行"，
    静默是节点没回话——线上可能是没节点、没上电、波特率不对或总线断了。
    """


class SdoProtocolError(CanopenError):
    """收到的帧不符合 SDO 规范（命令字不认识、索引对不上、长度不对等）。"""


class SdoSessionQuarantined(SdoProtocolError):
    """A timed-out SDO session cannot safely accept indistinguishable late replies."""


class SdoAbort(CanopenError):
    """服务端回了 abort（命令字 0x80）。"""

    def __init__(self, node_id: int, index: int, subindex: int, code: int,
                 request: Optional[Frame] = None) -> None:
        self.node_id = node_id
        self.index = index
        self.subindex = subindex
        self.code = code
        self.request = request
        super().__init__(
            "node %d: SDO abort %s at %s" % (
                node_id, decode_abort_code(code), format_object(index, subindex)))

    @property
    def description(self) -> str:
        return decode_abort_code(self.code)


# --------------------------------------------------------------------------
# NMT 状态机（CiA 301 §7.2.2 / §7.3.2）
# --------------------------------------------------------------------------


class NmtState:
    """NMT 状态常量。

    心跳帧的数据字节就是这些值——节点通过心跳自报状态。
    值 0x00 既是"上电初始化"又是"下电上报"，规范如此，靠上下文区分。
    """


    INITIALISING = 0x00
    STOPPED = 0x04
    OPERATIONAL = 0x05
    PRE_OPERATIONAL = 0x7F

    NAMES: Dict[int, str] = {
        0x00: "Initialising",
        0x04: "Stopped",
        0x05: "Operational",
        0x7F: "Pre-operational",
    }

    @classmethod
    def name(cls, value: int) -> str:
        """状态名。未知值原样十六进制。"""
        return cls.NAMES.get(value, "unknown state 0x%02X" % value)


#: NMT 命令字。只有主站发，广播到 COB-ID 0x000，数据为 `[命令, 节点号]`。
#: 节点号 0 表示全体。
NMT_COMMANDS: Dict[str, int] = {
    "start_remote_node": 0x01,
    "stop_remote_node": 0x02,
    "enter_pre_operational": 0x80,
    "reset_node": 0x81,
    "reset_communication": 0x82,
}


def nmt_command_frame(command: int, node_id: int = 0) -> Frame:
    """构造 NMT 命令帧。`node_id=0` 即广播。"""
    if command not in NMT_COMMANDS.values():
        raise ValueError("未知 NMT 命令 0x%02X" % command)
    _check_node_id(node_id)
    return Frame(COB_ID_NMT_BROADCAST, bytes([command, node_id]))


class NmtMaster:
    """NMT 主站：发命令，可选地等被命令的节点用心跳确认新状态。

    只发命令不等心跳是合法的（NMT 命令无应答）；`expect_state` 只是把
    "命令生效了没有"这件事变成一次可判定的等待，方便工具与测试使用。
    """

    def __init__(self, bus) -> None:
        self.bus = bus

    def send_command(self, command: int, node_id: int = 0) -> Frame:
        frame = nmt_command_frame(command, node_id)
        self.bus.send(frame)
        return frame

    def start(self, node_id: int = 0) -> Frame:
        return self.send_command(NMT_COMMANDS["start_remote_node"], node_id)

    def stop(self, node_id: int = 0) -> Frame:
        return self.send_command(NMT_COMMANDS["stop_remote_node"], node_id)

    def enter_pre_operational(self, node_id: int = 0) -> Frame:
        return self.send_command(NMT_COMMANDS["enter_pre_operational"], node_id)

    def reset_node(self, node_id: int = 0) -> Frame:
        return self.send_command(NMT_COMMANDS["reset_node"], node_id)

    def reset_communication(self, node_id: int = 0) -> Frame:
        return self.send_command(NMT_COMMANDS["reset_communication"], node_id)

    def wait_state(self, node_id: int, expected: int, timeout: float = 1.0,
                   clock: Optional[Callable[[], float]] = None) -> Frame:
        """等指定节点心跳进入 `expected` 状态。超时抛 BusSilenceError。"""
        import time as _time
        clock = clock or _time.monotonic
        deadline = clock() + timeout
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                raise BusSilenceError(
                    "node %d 未在 %.3fs 内上报状态 %s" % (
                        node_id, timeout, NmtState.name(expected)))
            frame = self.bus.recv(remaining)
            if frame is None:
                continue
            if frame.arbitration_id != cob_id_heartbeat(node_id):
                continue
            if not frame.data:
                continue
            if frame.data[0] == expected:
                return frame


# --------------------------------------------------------------------------
# 心跳消费者与超时判定
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HeartbeatEvent:
    """一次心跳到达。`new` 表示状态相比上一次发生了变化。"""

    node_id: int
    state: int
    timestamp: float
    previous_state: Optional[int] = None

    @property
    def is_transition(self) -> bool:
        """是否是一次状态**变化**。

        首次上报也算：此前状态未知（None），从"不知道"变成"知道"本身就是
        一次值得记录的变化——安全相关的看门狗要靠它判断"节点起来了吗"。
        """
        return self.previous_state is None or self.previous_state != self.state

    @property
    def state_name(self) -> str:
        return NmtState.name(self.state)


class HeartbeatMonitor:
    """心跳消费者 + 超时判定。

    每个节点独立计时：最后一次心跳到达的时刻超过 `timeout_s` 即判超时。
    节点在 `timeout_s` 内**完全没来过心跳**时，从对象 0x1017 读到的周期值
    是唯一依据，因此 `timeout_s` 应当 >= 2~3 倍心跳周期。

    这里不做阻塞轮询——`feed(frame)` 由调用方在收到任何帧时喂进来，
    `due_nodes(now)` 返回此刻已超时的节点。工具和测试都能自己控制时间轴。
    """

    def __init__(self, nodes: Iterable[int], timeout_s: float) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s 必须为正")
        self.timeout_s = float(timeout_s)
        self._last_seen: Dict[int, float] = {}
        self._states: Dict[int, int] = {}
        self._events: List[HeartbeatEvent] = []
        for node in nodes:
            _check_node_id(node)
            self._last_seen[node] = None  # type: ignore[assignment]

    @property
    def nodes(self) -> List[int]:
        return sorted(self._last_seen)

    def state_of(self, node_id: int) -> Optional[int]:
        return self._states.get(node_id)

    def last_seen(self, node_id: int) -> Optional[float]:
        return self._last_seen.get(node_id)

    def feed(self, frame: Frame, now: float) -> Optional[HeartbeatEvent]:
        """喂一帧。是心跳就记录并返回事件，否则返回 None。"""
        can_id = frame.arbitration_id
        if not 0x700 <= can_id <= 0x77F:
            return None
        node_id = can_id - 0x700
        if node_id not in self._last_seen:
            return None
        if len(frame.data) != 1:
            return None  # 心跳固定 1 字节；长度不对的当噪声，不当心跳
        state = frame.data[0]
        previous = self._states.get(node_id)
        self._states[node_id] = state
        self._last_seen[node_id] = now
        event = HeartbeatEvent(node_id=node_id, state=state, timestamp=now,
                               previous_state=previous)
        if event.is_transition:
            self._events.append(event)
        return event

    def due_nodes(self, now: float) -> List[int]:
        """返回此刻已超过 `timeout_s` 未上报的节点（含从未上报过的）。"""
        due = []
        for node_id, seen in sorted(self._last_seen.items()):
            if seen is None or (now - seen) > self.timeout_s:
                due.append(node_id)
        return due

    @property
    def transitions(self) -> List[HeartbeatEvent]:
        """迄今记录到的状态迁移，按发生顺序。"""
        return list(self._events)


# --------------------------------------------------------------------------
# EMCY
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Emcy:
    """紧急报文。

    8 字节布局（CiA 301 §7.2.7）：错误码 2 字节（小端）+ 错误寄存器 1 字节 +
    厂家特定 5 字节。**厂家错误码 -> 可读原因的映射表不在本模块**：
    PMP4010 的 EMCY 码表尚未提供（`docs/硬件手册/PMP4010/README.md` 待确认项 5），
    这里只把帧拆成规范定义的字段。
    """

    node_id: int
    error_code: int
    error_register: int
    vendor_data: bytes
    raw: bytes = b""

    @property
    def has_error(self) -> bool:
        """错误码 0x0000 表示"错误已消除"，是 EMCY 的清除报文。"""
        return self.error_code != 0x0000

    @property
    def is_clear(self) -> bool:
        return self.error_code == 0x0000

    def __str__(self) -> str:
        if self.is_clear:
            return "node %d: EMCY cleared" % self.node_id
        return "node %d: EMCY code 0x%04X reg 0x%02X vendor %s" % (
            self.node_id, self.error_code, self.error_register,
            " ".join("%02X" % b for b in self.vendor_data))


def parse_emcy(frame: Frame) -> Optional[Emcy]:
    """把一帧解成 EMCY；不是 EMCY 帧则返回 None。"""
    can_id = frame.arbitration_id
    if not 0x080 <= can_id <= 0x0FF:
        return None
    node_id = can_id - 0x080
    if len(frame.data) < 3:
        return None
    error_code = frame.data[0] | (frame.data[1] << 8)
    return Emcy(
        node_id=node_id,
        error_code=error_code,
        error_register=frame.data[2],
        vendor_data=frame.data[3:8],
        raw=frame.data,
    )


# --------------------------------------------------------------------------
# SDO 客户端
# --------------------------------------------------------------------------


def _quarantine_interrupted_sdo(method):
    """SIGINT can interrupt send after enqueue or leave an ACK in flight.

    The standard layer owns this uncertainty. Only explicit external recovery
    may reopen this same-node session; cleanup must use independent safety.
    """
    @wraps(method)
    def guarded(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except KeyboardInterrupt:
            if self._send_attempted:
                self.quarantined = True
            raise
    return guarded


class SdoClient:
    """SDO 客户端：加急与分段，上传与下载双向。

    每个请求/应答都是同步的（发一帧，等一帧）。CANopen SDO 本身是
    请求-应答式，因此不需要多线程；超时由 `bus.recv(timeout)` 负责。

    分段传输的 toggle 位：**每个分段传输的序列从 0 开始**，
    upload 与 download 各自交替。段内 n 位表示未使用的数据字节数，
    因此满 7 字节写 0，末段不足 7 字节时写 `7 - 有效字节数`。
    """

    def __init__(self, bus, node_id: int, timeout: float = 1.0) -> None:
        _check_node_id(node_id)
        self.bus = bus
        self.node_id = node_id
        self.timeout = timeout
        self.request_cob_id = cob_id_sdo_tx(node_id)
        self.response_cob_id = cob_id_sdo_rx(node_id)
        self.last_request: Optional[Frame] = None
        self._deadline = 0.0
        self._transmitted = False
        self._send_attempted = False
        self.quarantined = False

    def confirm_external_recovery(self) -> None:
        """Caller attests that peer/transport recovery eliminated all old replies.

        Flushing a receive queue, issuing NMT, waiting, or constructing a fresh
        client alone cannot establish this: SDO replies have no transaction ID.
        Until recovery, SDO cleanup/stop is blocked too; use independent safety.
        The single owner must preserve quarantine for the whole bus session.
        """
        self.quarantined = False

    def _begin(self) -> None:
        if self.quarantined:
            raise SdoSessionQuarantined(
                "node %d: SDO session quarantined after timeout; explicit external recovery required" %
                self.node_id)
        self._transmitted = False
        self._send_attempted = False
        self._deadline = time.monotonic() + self.timeout

    def _timeout(self) -> None:
        if self._transmitted:
            self.quarantined = True
        raise BusSilenceError(
            "node %d: %.3fs 内未收到 SDO 应答（请求 %s）" % (
                self.node_id, self.timeout,
                str(self.last_request) if self.last_request else "-"))

    # -- 帧收发 ------------------------------------------------------------

    def _send(self, data: bytes) -> Frame:
        if time.monotonic() >= self._deadline:
            self._timeout()
        frame = Frame(self.request_cob_id, data)
        self.last_request = frame
        self._send_attempted = True  # send may enqueue before KeyboardInterrupt
        self.bus.send(frame)
        self._transmitted = True
        return frame

    def _recv(self, request: Optional[Frame] = None) -> Frame:
        while True:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                frame = None
            else:
                frame = self.bus.recv(remaining)
            if frame is None or time.monotonic() >= self._deadline:
                self._timeout()
            # 总线还有其他节点的心跳/PDO/SDO；只消费本事务的标准数据帧。
            # 超时用总期限，杂帧不能把一次事务无限延长。
            if (frame.arbitration_id == self.response_cob_id and
                    not frame.extended and not frame.remote and
                    frame.frame_type == FRAME_CLASSIC):
                return frame

    def _check_abort(self, frame: Frame, index: int, subindex: int,
                     check_index: bool = True) -> None:
        data = frame.data
        if len(data) != 8:
            raise SdoProtocolError(
                "node %d: SDO 应答长度必须是 8 字节: %s" % (self.node_id, frame))
        # Abort always carries the original object's multiplexor, including during
        # a segmented transfer. Data segments themselves have no index fields.
        if data[0] == SDO_CS_ABORT or check_index:
            got_index = data[1] | (data[2] << 8)
            if got_index != index or data[3] != subindex:
                raise SdoProtocolError(
                    "node %d: 应答索引不符，期望 %s，收到 0x%04X:%02X" % (
                        self.node_id, format_object(index, subindex),
                        got_index, data[3]))
        if data[0] == SDO_CS_ABORT:
            code = struct.unpack("<I", data[4:8])[0]
            raise SdoAbort(self.node_id, index, subindex, code,
                           request=self.last_request)

    # -- 上传（读） --------------------------------------------------------

    @_quarantine_interrupted_sdo
    def read(self, index: int, subindex: int = 0) -> bytes:
        """读对象。加急应答一次返回；分段应答由 `_read_segmented` 续完。"""
        self._begin()
        request = self._send(bytes([
            SDO_CS_UPLOAD_REQUEST,
            index & 0xFF, (index >> 8) & 0xFF,
            subindex & 0xFF,
            0, 0, 0, 0,
        ]))
        frame = self._recv(request)
        if not frame.data:
            raise SdoProtocolError("node %d: 空应答帧" % self.node_id)
        self._check_abort(frame, index, subindex)
        cs = frame.data[0]
        if (cs & SDO_EXPEDITED_CS_MASK) == SDO_CS_UPLOAD_EXPEDITED_BASE:
            # 加急应答固定 4 字节数据段，但只有前 (4 - n) 字节有效，其余是填充。
            # 判读必须先掩掉 n 位：2 字节对象（例如 0x1017 心跳周期）的应答
            # 命令字是 0x4B 而不是 0x43。不裁填充，"2 字节对象读出 4 字节"
            # 这类错会一路传到上层。
            unused = (frame.data[0] & SDO_EXPEDITED_NBYTES_MASK) >> 2
            length = 4 - unused
            return bytes(frame.data[4:4 + length])
        if cs in (SDO_CS_UPLOAD_SEGMENTED_NO_SIZE, SDO_CS_UPLOAD_SEGMENTED):
            # Both initiate responses are eight bytes. Only the s bit determines
            # whether bytes 4..7 contain a declared total (0x41) or reserved data (0x40).
            total = (struct.unpack("<I", frame.data[4:8])[0]
                     if cs & SDO_UPLOAD_SEGMENTED_WITH_SIZE else None)
            return self._read_segmented(total, index, subindex)
        raise SdoProtocolError(
            "node %d: 上传应答命令字不认识 0x%02X (%s)" % (
                self.node_id, cs, frame))

    def _read_segmented(self, total: Optional[int], index: int, subindex: int) -> bytes:
        """续读分段上传。toggle 从 0 起，交替 0/1。"""
        payload = bytearray()
        toggle = 0
        if total is not None and total > SDO_PAYLOAD_CAPACITY:
            raise SdoProtocolError("node %d: 分段上传超过容量 %d" %
                                   (self.node_id, SDO_PAYLOAD_CAPACITY))
        # Non-final segments always contain seven bytes. This additionally caps
        # segment count even when the peer omits the total or never marks last.
        for _ in range(SDO_PAYLOAD_CAPACITY // 7 + 1):
            request = self._send(bytes([
                SDO_CS_UPLOAD_SEGMENT_REQUEST | (toggle << 4),
                0, 0, 0, 0, 0, 0, 0,
            ]))
            frame = self._recv(request)
            data = frame.data
            if not data:
                raise SdoProtocolError("node %d: 空分段应答" % self.node_id)
            # 数据段的应答不含索引，因此不做索引回显校验
            self._check_abort(frame, index, subindex, check_index=False)
            cs = data[0]
            # CiA 301 upload segment response: 000tnnnc. The initiate response
            # family (0100nnes) must never be accepted as a data segment.
            if (cs & SDO_CS_UPLOAD_SEGMENT_MASK) != SDO_CS_UPLOAD_SEGMENT_RESPONSE:
                raise SdoProtocolError(
                    "node %d: 分段应答命令字不认识 0x%02X (%s)" % (
                        self.node_id, cs, frame))
            got_toggle = (cs >> 4) & 0x01
            if got_toggle != toggle:
                raise SdoProtocolError(
                    "node %d: toggle 位未交替，期望 %d 收到 %d" % (
                        self.node_id, toggle, got_toggle))
            last = bool(cs & SDO_CS_SEGMENT_LAST)
            if not last and cs & SDO_CS_SEGMENT_NBYTES_MASK:
                raise SdoProtocolError("node %d: 非末段含无效字节计数" % self.node_id)
            # 末段的 n 位编码的是**未使用的字节数**（(cs & 0x0E) >> 1），
            # 所以有效字节数是 7 减去它；非末段固定 7 字节。
            # 把它当成"有效字节数 - 1"会让末段多算几个字节，
            # 长度校验随即以"声明 N 实收 N+k"失败。
            nbytes = 7 - ((cs & SDO_CS_SEGMENT_NBYTES_MASK) >> 1) if last else 7
            if len(payload) + nbytes > SDO_PAYLOAD_CAPACITY:
                raise SdoProtocolError("node %d: 分段上传超过容量 %d" %
                                       (self.node_id, SDO_PAYLOAD_CAPACITY))
            payload.extend(data[1:1 + nbytes])
            if total is not None and len(payload) > total:
                raise SdoProtocolError(
                    "node %d: 分段上传长度超过声明 %d" % (self.node_id, total))
            toggle ^= 1
            if last:
                break
        else:
            raise SdoProtocolError("node %d: 分段上传段数超过容量 %d" %
                                   (self.node_id, SDO_PAYLOAD_CAPACITY))
        if total is not None and len(payload) != total:
            raise SdoProtocolError(
                "node %d: 分段上传长度不符，声明 %d 实收 %d" % (
                    self.node_id, total, len(payload)))
        return bytes(payload)

    # -- 下载（写） --------------------------------------------------------

    @_quarantine_interrupted_sdo
    def write(self, index: int, subindex: int, data: bytes) -> None:
        """写对象。<=4 字节走加急，更长走分段。"""
        payload = bytes(data)
        if not 0 < len(payload) <= SDO_PAYLOAD_CAPACITY:
            raise SdoProtocolError("SDO download payload must contain 1..%d bytes" % SDO_PAYLOAD_CAPACITY)
        self._begin()
        index_bytes = bytes([index & 0xFF, (index >> 8) & 0xFF, subindex & 0xFF])
        if len(payload) == 4:
            # 4 字节用 0x23（无 n 位），避免与"带长度指示"混淆
            request = self._send(bytes([SDO_CS_DOWNLOAD_EXPEDITED]) + index_bytes + payload)
        elif len(payload) < 4:
            # 基值恒为 0x23（4 字节那个），n 位编码 (4 - 有效字节数) << 2。
            # 基值不能取 SDO_CS_DOWNLOAD_SIZE_INDICATED —— 那是"3 字节"的完整
            # 命令字（0x27），拿它再做或运算会把长度算多一位：
            # 2 字节会发成 0x2F、1 字节会发成 0x33。
            command = SDO_CS_DOWNLOAD_EXPEDITED | ((4 - len(payload)) << 2)
            request = self._send(
                bytes([command]) + index_bytes + payload + b"\x00" * (4 - len(payload)))
        else:
            # 分段下载的发起帧（CiA 301 §7.2.4.2）：
            #   0x21 | 索引(2) | 子索引(1) | 长度(4，小端)
            # 顺序不能颠倒——把长度写在索引前，服务端会解析出一个不存在的对象。
            request = self._send(
                bytes([SDO_CS_DOWNLOAD_SEGMENTED]) + index_bytes
                + struct.pack("<I", len(payload)))
        frame = self._recv(request)
        if not frame.data:
            raise SdoProtocolError("node %d: 空应答帧" % self.node_id)
        self._check_abort(frame, index, subindex)
        cs = frame.data[0]
        # 这一轮的确认固定是 0x60：加急下载与分段下载的发起帧都用它。
        # 数据段的确认（0x20|toggle<<4）在 _write_segmented 里单独校验。
        if cs != SDO_CS_DOWNLOAD_INITIATE_ACK:
            raise SdoProtocolError(
                "node %d: 下载确认命令字不认识 0x%02X (%s)" % (
                    self.node_id, cs, frame))
        if len(payload) > 4:
            # 只有**超过 4 字节**才是分段下载：发起帧 0x21 已由上面的分支发出，
            # 余下数据由 _write_segmented 按段续完。
            # 判据写成 >= 4 的话，4 字节对象会先走加急帧、再被当成分段续传，
            # 表现为"4 字节写成功一半后总线静默"。
            self._write_segmented(payload, index, subindex)

    def _write_segmented(self, payload: bytes, index: int, subindex: int) -> None:
        """续传分段下载。toggle 从 0 起。末段不足 7 字节时用 n 位标出有效长度。"""
        offset = 0
        toggle = 0
        total = len(payload)
        while offset < total:
            chunk = payload[offset:offset + 7]
            offset += len(chunk)
            last = offset >= total
            command = (toggle << 4)
            if last:
                command |= SDO_CS_SEGMENT_LAST
                command |= ((7 - len(chunk)) << 1) & SDO_CS_SEGMENT_NBYTES_MASK
            body = chunk + b"\x00" * (7 - len(chunk))
            request = self._send(bytes([command]) + body)
            frame = self._recv(request)
            data = frame.data
            if not data:
                raise SdoProtocolError("node %d: 空分段确认" % self.node_id)
            # 数据段的确认帧不含索引，因此不做索引回显校验
            self._check_abort(frame, index, subindex, check_index=False)
            cs = data[0]
            # 确认帧命令字 = 0x20 | (toggle << 4)：toggle=0 是 0x20，toggle=1 是 0x30。
            # 判据必须覆盖 0x2x 与 0x3x 两段；只判 `(cs & 0xE0) == 0x20`
            # 会把第一段的 0x20 拒掉。
            if not 0x20 <= cs <= 0x3F:
                raise SdoProtocolError(
                    "node %d: 分段确认命令字不认识 0x%02X (%s)" % (
                        self.node_id, cs, frame))
            got_toggle = (cs >> 4) & 0x01
            if got_toggle != toggle:
                raise SdoProtocolError(
                    "node %d: toggle 位未交替，期望 %d 收到 %d" % (
                        self.node_id, toggle, got_toggle))
            toggle ^= 1

    # -- 便捷读写 ----------------------------------------------------------

    def read_uint8(self, index: int, subindex: int = 0) -> int:
        return self._as_int(self.read(index, subindex), 1)

    def read_uint16(self, index: int, subindex: int = 0) -> int:
        return self._as_int(self.read(index, subindex), 2)

    def read_uint32(self, index: int, subindex: int = 0) -> int:
        return self._as_int(self.read(index, subindex), 4)

    def read_string(self, index: int, subindex: int = 0) -> str:
        raw = self.read(index, subindex)
        return raw.rstrip(b"\x00").decode("utf-8", errors="replace")

    @staticmethod
    def _as_int(raw: bytes, size: int) -> int:
        if len(raw) != size:
            raise SdoProtocolError(
                "长度不符，期望 %d 字节，收到 %d 字节: %s" % (
                    size, len(raw), raw.hex()))
        return int.from_bytes(raw, "little")

    def write_uint8(self, index: int, subindex: int, value: int) -> None:
        self.write(index, subindex, int(value).to_bytes(1, "little"))

    def write_uint16(self, index: int, subindex: int, value: int) -> None:
        self.write(index, subindex, int(value).to_bytes(2, "little"))

    def write_uint32(self, index: int, subindex: int, value: int) -> None:
        self.write(index, subindex, int(value).to_bytes(4, "little"))

    # -- 标准对象快捷方式（全部是规范公共对象） -----------------------------

    def read_device_type(self) -> int:
        """0x1000 设备类型：附加信息 16 位 + 设备规范 16 位。

        设备规范号的高 16 位（0x0000 表示无规范）；
        低 16 位里的 CiA 402 之类要靠规范号判断，这里只回原始值。
        """
        return self.read_uint32(0x1000)

    def read_manufacturer_name(self) -> str:
        return self.read_string(0x1008)

    def read_software_version(self) -> str:
        return self.read_string(0x100A)

    def read_hardware_version(self) -> str:
        return self.read_string(0x1009)

    def read_identity(self) -> Dict[str, object]:
        """0x1018 标识对象：厂家 ID / 产品码 / 版本 / 序列号。

        子索引 0 是条目数，1..4 分别是这四个 32 位字段。
        实际条目数按读到的为准——规范允许扩展，但至少 4 条。
        """
        count = self.read_uint8(0x1018, 0)
        result: Dict[str, object] = {"count": count}
        names = {1: "vendor_id", 2: "product_code",
                 3: "revision_number", 4: "serial_number"}
        for subindex, key in names.items():
            if subindex <= count:
                result[key] = self.read_uint32(0x1018, subindex)
        return result

    def read_producer_heartbeat_time(self) -> int:
        """0x1017 心跳生产者周期，单位 ms。0 表示不发心跳。"""
        return self.read_uint16(0x1017)

    def read_error_register(self) -> int:
        return self.read_uint8(0x1001)


# --------------------------------------------------------------------------
# 节点扫描
# --------------------------------------------------------------------------


def scan_nodes(bus, nodes: Iterable[int] = range(1, 128), timeout: float = 0.05,
               probe: Optional[Callable[[SdoClient], None]] = None) -> List[int]:
    """逐个节点试探，返回有应答的节点号。

    **默认用 SDO 探，不用心跳探。** 理由：心跳可能被 0x1017 关掉（周期 0），
    也可能周期长于扫描窗口；而一个在 Pre-operational 状态的节点照样回 SDO。
    要扫心跳请用 `HeartbeatMonitor`。

    静默总线上的开销是 `len(nodes) * timeout`——127 个节点、50ms 超时约 6.4 秒，
    这是有意的：工具界面上要能看到进度，而不是长时间挂死。

    `probe` 可换成自定义的判活动作（例如读一个厂家指定对象）。
    """
    found: List[int] = []
    for node_id in nodes:
        client = SdoClient(bus, node_id, timeout=timeout)
        try:
            if probe is not None:
                probe(client)
            else:
                # 读设备类型 0x1000 是规范要求每个节点都实现的最小公共对象
                client.read(0x1000, 0)
        except CanopenError:
            continue
        found.append(node_id)
    return found


# --------------------------------------------------------------------------
# PDO 映射与通讯参数的只读访问
# --------------------------------------------------------------------------
#
# 本层**只读**。写入 PDO 映射会改变设备行为，属于"已知设备、已知映射"之后的事，
# 而 PMP4010 的默认映射表尚未到手（README 待确认项 7）。只读是为了
# 在拿到手册前也能把 8 个节点的现状抄下来，与手册对照。


@dataclass
class PdoMapping:
    """一个 PDO 的映射内容（对象 0x1600+ / 0x1A00+）。

    子索引 0 是映射条目数，1..n 每条 32 位：
    `索引 16 位 | 子索引 8 位 | 位长 8 位`。位长 0 表示该条目不使用。
    """

    index: int
    count: int
    entries: List[Tuple[int, int, int]] = field(default_factory=list)

    @property
    def total_bits(self) -> int:
        return sum(bits for _, _, bits in self.entries)

    def __str__(self) -> str:
        parts = ["0x%04X:%02X/%d bits" % (i, s, b) for i, s, b in self.entries]
        return "0x%04X (%d entries, %d bits): %s" % (
            self.index, self.count, self.total_bits, ", ".join(parts))


def read_pdo_mapping(client: SdoClient, base_index: int, subindex: int) -> PdoMapping:
    """读一个 PDO 映射对象。`base_index` 取 0x1600（RPDO）或 0x1A00（TPDO）。"""
    if not (0x1600 <= base_index <= 0x17FF or 0x1A00 <= base_index <= 0x1BFF):
        raise ValueError(
            "0x%04X 不是 PDO 映射对象（应为 0x1600-0x17FF 或 0x1A00-0x1BFF）" % base_index)
    object_index = base_index + subindex
    count = client.read_uint8(object_index, 0)
    mapping = PdoMapping(index=object_index, count=count)
    for entry in range(1, count + 1):
        raw = client.read_uint32(object_index, entry)
        mapping.entries.append((
            (raw >> 16) & 0xFFFF,
            (raw >> 8) & 0xFF,
            raw & 0xFF,
        ))
    return mapping


@dataclass
class PdoCommunication:
    """一个 PDO 的通讯参数（对象 0x1400+ / 0x1800+）。

    - 0x01 COB-ID：bit31 禁用，bit30 禁止 RTR，低 11 位是 COB-ID 本体
    - 0x02 传输类型：0 同步非周期，1..240 同步周期，254/255 事件驱动
    - 0x05 事件定时器，单位 ms（0 表示不用）
    - 0x03 抑制时间，单位 100us（仅 TPDO 有）
    """

    index: int
    cob_id_raw: int
    transmission_type: int
    inhibit_time: Optional[int] = None
    event_timer: Optional[int] = None
    count: int = 0

    @property
    def valid(self) -> bool:
        """bit31 = 0 表示该 PDO 有效/启用。"""
        return not (self.cob_id_raw & 0x80000000)

    @property
    def rtr_allowed(self) -> bool:
        """公共 COB-ID 编码：bit30=1 禁止 RTR，不表示设备实现了 RTR。

        依据 CANopenNode/CANopenDemo tutorial/PDO.md 的 PDO communication parameter。
        """
        return not bool(self.cob_id_raw & 0x40000000)

    @property
    def cob_id(self) -> int:
        return self.cob_id_raw & 0x7FF

    @property
    def transmission_type_name(self) -> str:
        tt = self.transmission_type
        if tt == 0:
            return "synchronous acyclic"
        if 1 <= tt <= 240:
            return "synchronous cyclic (every %d SYNC)" % tt
        if tt == 252:
            return "RTR only, synchronous"
        if tt == 253:
            return "RTR only, event driven"
        if tt in (254, 255):
            return "event driven"
        if 241 <= tt <= 251:
            return "reserved (0x%02X)" % tt
        return "unknown transmission type 0x%02X" % tt

    def __str__(self) -> str:
        state = "valid" if self.valid else "invalid/disabled"
        return "0x%04X: COB-ID 0x%03X (%s), %s" % (
            self.index, self.cob_id, state, self.transmission_type_name)


def read_pdo_communication(client: SdoClient, base_index: int,
                           subindex: int) -> PdoCommunication:
    """读一个 PDO 通讯参数对象。`base_index` 取 0x1400（RPDO）或 0x1800（TPDO）。"""
    if not (0x1400 <= base_index <= 0x15FF or 0x1800 <= base_index <= 0x19FF):
        raise ValueError(
            "0x%04X 不是 PDO 通讯参数对象（应为 0x1400-0x15FF 或 0x1800-0x19FF）" % base_index)
    object_index = base_index + subindex
    count = client.read_uint8(object_index, 0)
    comm = PdoCommunication(
        index=object_index,
        cob_id_raw=client.read_uint32(object_index, 1),
        transmission_type=client.read_uint8(object_index, 2),
        count=count,
    )
    if count >= 5:
        comm.event_timer = client.read_uint16(object_index, 5)
    if 0x1800 <= object_index <= 0x19FF and count >= 3:
        comm.inhibit_time = client.read_uint16(object_index, 3)
    return comm
