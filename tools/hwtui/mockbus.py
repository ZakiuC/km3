"""km3 模拟总线：与 `canbus.CanBus` 同接口，供无硬件回归使用。

三个用途，对应三类回归：

1. **注入应答**——`MockBus` 里挂 `MockNode`，用内置对象字典应答 SDO。
   不需要 pty、不需要 CAN 内核模块、不需要 vcan（本板 `CONFIG_CAN_VCAN` 未开，
   实测见 `实测基线.md` §九）。
2. **制造超时与 abort**——`MockNode` 可以配置成不应答（静默总线上的扫描）、
   或对特定对象回 abort code。
3. **录制并回放**——`TraceRecorder` 把真实总线上跑过的帧序列录成 JSON，
   `MockBus.from_trace()` 照着回放，把现场的一次故障固化成一条可重跑的回归。

上层（`canopen.py`）对 `CanBus` 与 `MockBus` 无感：两者都提供
`send(frame)` / `recv(timeout)` / `close()` / 上下文管理器。

**时间由 `clock` 注入**，默认 `time.monotonic`。测试可以传一个手工推进的时钟，
把"心跳 3 秒超时"压成微秒级断言，不必真的 sleep。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from canopen import (
    FLAG_EXTENDED,
    FLAG_REMOTE,
    FRAME_CLASSIC,
    FRAME_FD,
    NmtState,
    SDO_CS_ABORT,
    SDO_CS_DOWNLOAD_EXPEDITED,
    SDO_CS_DOWNLOAD_SEGMENT_ACK,
    SDO_CS_DOWNLOAD_INITIATE_ACK,
    SDO_CS_DOWNLOAD_SEGMENTED,
    SDO_CS_DOWNLOAD_SIZE_INDICATED,
    SDO_CS_SEGMENT_LAST,
    SDO_CS_SEGMENT_NBYTES_MASK,
    SDO_DOWNLOAD_CS_MASK,
    SDO_DOWNLOAD_SIZE_MASK,
    SDO_CS_UPLOAD_EXPEDITED,
    SDO_CS_UPLOAD_REQUEST,
    SDO_CS_UPLOAD_SEGMENTED,
    SDO_CS_UPLOAD_SEGMENT_REQUEST,
    SDO_UPLOAD_SEGMENTED_WITH_SIZE,
    Frame,
    cob_id_emcy,
    cob_id_heartbeat,
    cob_id_sdo_rx,
    cob_id_sdo_tx,
)

__all__ = [
    "MockBusError",
    "TraceMismatchError",
    "OdEntry",
    "MockNode",
    "MockBus",
    "TraceRecorder",
    "frame_from_json",
    "frame_to_json",
    "make_sdo_golden_node",
]


class MockBusError(Exception):
    """模拟总线基类异常。"""


class TraceMismatchError(MockBusError, AssertionError):
    """回放时收到的帧与录制的期望不符。

    同时继承 AssertionError，好让 `unittest` 的 `assertRaises` 直接认它。
    """


# --------------------------------------------------------------------------
# 对象字典条目
# --------------------------------------------------------------------------


@dataclass
class OdEntry:
    """模拟节点的一个对象字典条目。

    - `data`：初值。字节串，长度即对象长度。
    - `abort`：非 0 时，任何对该对象的访问都回这个 abort code。
    - `readable` / `writable`：访问权限。写只读对象回 0x06010002。
    """

    data: bytes = b""
    abort: int = 0
    readable: bool = True
    writable: bool = False

    @property
    def size(self) -> int:
        return len(self.data)


# --------------------------------------------------------------------------
# 模拟节点
# --------------------------------------------------------------------------


class MockNode:
    """一个模拟的 CANopen 从站。

    实现范围刻意保持在"标准层能验证"的粒度：SDO 加急/分段双向、NMT 状态机、
    心跳、EMCY。不做 PDO 数据更新（那是实时路径，且映射内容未定）。

    内置的公共对象（规范值，非厂家索引）：
      0x1000 设备类型、0x1001 错误寄存器、0x1008 厂家名称、0x100A 软件版本、
      0x1017 心跳周期、0x1018 标识对象。其余由调用方通过 `od` 注入。
    """

    def __init__(self, node_id: int, od: Optional[Dict[Tuple[int, int], OdEntry]] = None,
                 heartbeat_period_ms: int = 0,
                 segmented_upload_size_indicated: bool = True,
                 device_type: int = 0x00000000,
                 manufacturer_name: str = "km3 mock",
                 software_version: str = "0.0.0",
                 vendor_id: int = 0x00000000,
                 product_code: int = 0x00000000,
                 serial_number: int = 0x00000000) -> None:
        self.node_id = node_id
        self.od: Dict[Tuple[int, int], OdEntry] = {
            (0x1000, 0): OdEntry(device_type.to_bytes(4, "little")),
            (0x1001, 0): OdEntry(b"\x00"),
            (0x1008, 0): OdEntry(manufacturer_name.encode("utf-8")),
            (0x1009, 0): OdEntry(b"rev-A"),
            (0x100A, 0): OdEntry(software_version.encode("utf-8")),
            (0x1017, 0): OdEntry((heartbeat_period_ms & 0xFFFF).to_bytes(2, "little"),
                                 writable=True),
            (0x1018, 0): OdEntry(b"\x04"),
            (0x1018, 1): OdEntry(vendor_id.to_bytes(4, "little")),
            (0x1018, 2): OdEntry(product_code.to_bytes(4, "little")),
            (0x1018, 3): OdEntry(b"\x00\x00\x01\x00"),
            (0x1018, 4): OdEntry(serial_number.to_bytes(4, "little")),
        }
        if od:
            self.od.update(od)

        self.state = NmtState.INITIALISING
        self.heartbeat_period_ms = heartbeat_period_ms
        #: 分段上传首帧是否带总长度指示（0x41|0x01 + 4 字节长度）。
        #: 真有从站不带，因此两种形态都必须能被客户端正确处理。
        self.segmented_upload_size_indicated = segmented_upload_size_indicated
        self.error_register = 0
        self.emcy_code = 0
        self.emcy_vendor = b"\x00" * 5

        # 分段传输的中间状态。上传是"生成器"，下载是"累加器"。
        self._seg_toggle = 0
        self._seg_download_buffer = bytearray()
        self._seg_download_index: Optional[int] = None
        self._seg_download_subindex = 0
        self._seg_download_total = 0
        #: 节点发出的帧（应答 + 自发的心跳/EMCY）
        self._outbox: List[Frame] = []
        #: 记录收到的所有帧，供断言"主站确实按预期发了什么"
        self.received: List[Frame] = []
        self._last_heartbeat_at = 0.0

    # -- 字典 --------------------------------------------------------------

    def set_object(self, index: int, subindex: int, data: bytes,
                   writable: bool = True) -> None:
        self.od[(index, subindex)] = OdEntry(data, writable=writable)

    def set_abort(self, index: int, subindex: int, code: int) -> None:
        entry = self.od.setdefault((index, subindex), OdEntry(b""))
        entry.abort = code

    def object_value(self, index: int, subindex: int) -> Optional[bytes]:
        entry = self.od.get((index, subindex))
        return None if entry is None else entry.data

    def read_int(self, index: int, subindex: int = 0) -> Optional[int]:
        raw = self.object_value(index, subindex)
        return None if raw is None else int.from_bytes(raw, "little")

    # -- 自发帧 ------------------------------------------------------------

    def queue_heartbeat(self, state: Optional[int] = None) -> Frame:
        """排一个心跳帧到发送队列。"""
        if state is None:
            state = self.state
        frame = Frame(cob_id_heartbeat(self.node_id), bytes([state & 0xFF]))
        self._outbox.append(frame)
        return frame

    def queue_emcy(self, error_code: int, error_register: int = 0,
                   vendor: bytes = b"") -> Frame:
        """排一个 EMCY 帧。error_code=0 即"错误已消除"。"""
        body = bytes([error_code & 0xFF, (error_code >> 8) & 0xFF,
                      error_register & 0xFF]) + vendor.ljust(5, b"\x00")[:5]
        frame = Frame(cob_id_emcy(self.node_id), body)
        self._outbox.append(frame)
        return frame

    def pop_outbox(self) -> Optional[Frame]:
        return self._outbox.pop(0) if self._outbox else None

    @property
    def outbox_len(self) -> int:
        return len(self._outbox)

    # -- 收帧处理 ----------------------------------------------------------

    def handle(self, frame: Frame, now: float) -> None:
        """处理一个来自主站的帧。收到的帧一律记进 `received`。"""
        self.received.append(frame)
        can_id = frame.arbitration_id
        if can_id == 0x000:
            self._handle_nmt(frame)
        elif can_id == cob_id_sdo_tx(self.node_id):
            self._handle_sdo(frame)
        # 其它 COB-ID 与本节点无关（总线是广播的，混着别人的帧很正常）

    def _handle_nmt(self, frame: Frame) -> None:
        """NMT 命令：数据为 `[命令, 节点号]`，节点号 0 是广播。"""
        data = frame.data
        if len(data) < 2:
            return
        command, target = data[0], data[1]
        if target not in (0, self.node_id):
            return
        # CiA 301 §7.3.2：reset 类命令先回 Initialising 再回 Pre-operational
        if command == 0x01:      # start_remote_node
            self.state = NmtState.OPERATIONAL
        elif command == 0x02:    # stop_remote_node
            self.state = NmtState.STOPPED
        elif command == 0x80:    # enter_pre_operational
            self.state = NmtState.PRE_OPERATIONAL
        elif command == 0x81:    # reset_node
            self.state = NmtState.INITIALISING
            self._reset_state()
            self.queue_heartbeat(NmtState.INITIALISING)
            self.state = NmtState.PRE_OPERATIONAL
        elif command == 0x82:    # reset_communication
            self.state = NmtState.PRE_OPERATIONAL
        else:
            return
        self.queue_heartbeat()

    def _reset_state(self) -> None:
        self._seg_toggle = 0
        self._seg_download_buffer = bytearray()
        self._seg_download_index = None

    def _abort_frame(self, index: int, subindex: int, code: int) -> Frame:
        body = bytes([SDO_CS_ABORT,
                      index & 0xFF, (index >> 8) & 0xFF, subindex & 0xFF]) \
            + code.to_bytes(4, "little")
        return Frame(cob_id_sdo_rx(self.node_id), body)

    def _entry_or_abort(self, index: int, subindex: int,
                        writing: bool) -> Tuple[Optional[OdEntry], Optional[Frame]]:
        """取字典条目；不存在或不可访问时返回要发的 abort 帧。"""
        entry = self.od.get((index, subindex))
        if entry is None:
            return None, self._abort_frame(index, subindex, 0x06020000)
        if entry.abort:
            return None, self._abort_frame(index, subindex, entry.abort)
        if writing and not entry.writable:
            return None, self._abort_frame(index, subindex, 0x06010002)
        if not writing and not entry.readable:
            return None, self._abort_frame(index, subindex, 0x06010001)
        return entry, None

    def _handle_sdo(self, frame: Frame) -> None:
        data = frame.data
        if len(data) < 4:
            return
        command = data[0]
        index = data[1] | (data[2] << 8)
        subindex = data[3]

        # --- 上传（读） ---
        if command == SDO_CS_UPLOAD_REQUEST:
            entry, abort = self._entry_or_abort(index, subindex, writing=False)
            if abort is not None:
                self._outbox.append(abort)
                return
            assert entry is not None
            payload = entry.data
            if len(payload) <= 4:
                unused = 4 - len(payload)
                body = bytes([SDO_CS_UPLOAD_EXPEDITED | (unused << 2),
                              index & 0xFF, (index >> 8) & 0xFF, subindex & 0xFF,
                              ]) + payload.ljust(4, b"\x00")
                self._outbox.append(Frame(cob_id_sdo_rx(self.node_id), body))
            else:
                # 分段上传首帧。带长度指示时命令字为 0x41|0x01 且 byte4..7 是总长；
                # 不带时命令字 0x41、byte4..7 保留。两种形态客户端都要认。
                if self.segmented_upload_size_indicated:
                    body = bytes([SDO_CS_UPLOAD_SEGMENTED | SDO_UPLOAD_SEGMENTED_WITH_SIZE,
                                  index & 0xFF, (index >> 8) & 0xFF, subindex & 0xFF,
                                  ]) + len(payload).to_bytes(4, "little")
                else:
                    # 不带长度指示：命令字 0x41，帧只发前 4 字节。
                    # 规范正是用**帧长**区分两种首帧（4 字节 vs 8 字节），
                    # 因此这里不能补 4 个零字节——补了就变成"带长度，总长为 0"。
                    body = bytes([SDO_CS_UPLOAD_SEGMENTED,
                                  index & 0xFF, (index >> 8) & 0xFF, subindex & 0xFF])
                self._outbox.append(Frame(cob_id_sdo_rx(self.node_id), body))
                self._seg_upload_payload = payload
                self._seg_upload_offset = 0
                self._seg_toggle = 0
            return

        # --- 上传段索取 ---
        # 命令字是 0x60 或 0x70（toggle 位在 0x10 上）。**必须精确匹配这两个值。**
        # 曾经写成 `0x60 <= command <= 0x7F` / `(command & 0xE0) == 0x60`，
        # 结果 0x2F（带长度指示的加急下载）也被吞进本分支——
        # 它不是分段请求，"发送 2 字节对象后总线静默"就是这么来的。
        if command in (SDO_CS_UPLOAD_SEGMENT_REQUEST,
                       SDO_CS_UPLOAD_SEGMENT_REQUEST | 0x10):
            payload = getattr(self, "_seg_upload_payload", b"")
            offset = getattr(self, "_seg_upload_offset", 0)
            chunk = payload[offset:offset + 7]
            offset += len(chunk)
            last = offset >= len(payload)
            # 分段上传应答的命令字是 `0x40 | (toggle << 4) | c`，**0x40 基值不能丢**：
            #   toggle 0 非末段 0x40、末段 0x41；toggle 1 非末段 0x50、末段 0x51。
            # 只写 toggle 位会发出 0x00/0x10，那是**下载**方向的命令字，
            # 客户端无从分辨方向——真机上这就是"读到一半解析乱套"。
            toggle = self._seg_toggle
            self._seg_toggle = 1 - toggle
            command_byte = SDO_CS_UPLOAD_SEGMENTED & 0xC0 | (toggle << 4)
            if last:
                command_byte |= SDO_CS_SEGMENT_LAST
                command_byte |= ((7 - len(chunk)) << 1) & SDO_CS_SEGMENT_NBYTES_MASK
            body = bytes([command_byte]) + chunk.ljust(7, b"\x00")
            self._outbox.append(Frame(cob_id_sdo_rx(self.node_id), body))
            self._seg_upload_payload = payload
            self._seg_upload_offset = offset
            return

        # --- 加急下载 / 带长度指示的下载（写） ---
        # 加急下载的命令字是 `0x23 | ((4 - 有效字节数) << 2)`，即
        #   4 字节 0x23、3 字节 0x27、2 字节 0x2B、1 字节 0x2F。
        # 判读必须先掩掉 n 位（0xE3 掩码）再比基值 0x23 —— 直接精确比对 0x27
        # 会漏掉 0x2B/0x2F，表现为"写 1/2 字节对象后总线静默"。
        if (command & SDO_DOWNLOAD_CS_MASK) == SDO_CS_DOWNLOAD_EXPEDITED \
                or command in (SDO_CS_DOWNLOAD_EXPEDITED,):
            entry, abort = self._entry_or_abort(index, subindex, writing=True)
            if abort is not None:
                self._outbox.append(abort)
                return
            assert entry is not None
            if (command & SDO_DOWNLOAD_SIZE_MASK) == 0:
                # n 位为 0：4 字节全有效
                value = bytes(data[4:8])
            else:
                unused = (command & SDO_DOWNLOAD_SIZE_MASK) >> 2
                value = bytes(data[4:4 + (4 - unused)])
            if len(value) != entry.size:
                self._outbox.append(
                    self._abort_frame(index, subindex, 0x06070010))
                return
            entry.data = value
            self._outbox.append(
                Frame(cob_id_sdo_rx(self.node_id),
                      bytes([SDO_CS_DOWNLOAD_INITIATE_ACK,
                             index & 0xFF, (index >> 8) & 0xFF, subindex & 0xFF,
                             0, 0, 0, 0])))
            return

        # --- 分段下载的发起帧 ---
        if command == SDO_CS_DOWNLOAD_SEGMENTED:
            if len(data) < 8:
                return
            entry, abort = self._entry_or_abort(index, subindex, writing=True)
            if abort is not None:
                self._outbox.append(abort)
                return
            self._seg_download_index = index
            self._seg_download_subindex = subindex
            self._seg_download_total = int.from_bytes(data[4:8], "little")
            self._seg_download_buffer = bytearray()
            self._seg_toggle = 0
            self._outbox.append(
                Frame(cob_id_sdo_rx(self.node_id),
                      bytes([SDO_CS_DOWNLOAD_INITIATE_ACK,
                             index & 0xFF, (index >> 8) & 0xFF, subindex & 0xFF,
                             0, 0, 0, 0])))
            return

        # --- 分段下载的数据段 ---
        # 数据段的命令字是 `(toggle << 4) | (c 位) | n 位`，
        # 所以 toggle=0 时是 0x00/0x01/0x0E/0x0F 这一类，**不是** 0x2x：
        # 0x2x 那一族是加急下载（0x23/0x27/0x2B/0x2F）。
        # 之前用 `(command & 0xE0) == 0x20` 判数据段，把加急下载全吞了，
        # 同时把真正的数据段（0x00/0x10）漏掉，表现为"分段写第一段后总线静默"。
        # 判据：toggle 位（0x10）之外的高位必须为 0，即命令字落在 0x00..0x1F。
        #
        # 注意 0x60/0x70（上传段索取）也在 0x60..0x7F，已在上面精确匹配掉，
        # 走到这里还带 0x20 位的一律按未知命令忽略。
        if command <= 0x1F:
            if self._seg_download_index is None:
                return
            toggle = (command >> 4) & 0x01
            if toggle != self._seg_toggle:
                self._outbox.append(self._abort_frame(
                    self._seg_download_index, self._seg_download_subindex,
                    0x05030000))
                return
            last = bool(command & SDO_CS_SEGMENT_LAST)
            # 末段的 n 位编码为 (7 - 有效字节数)，即 byte0 & 0x0E >> 1 是"空出的字节数"
            nbytes = 7 - ((command & SDO_CS_SEGMENT_NBYTES_MASK) >> 1) if last else 7
            self._seg_download_buffer.extend(data[1:1 + nbytes])
            self._seg_toggle = 1 - self._seg_toggle
            ack = bytes([SDO_CS_DOWNLOAD_SEGMENT_ACK | (toggle << 4),
                         0, 0, 0, 0, 0, 0, 0])
            self._outbox.append(Frame(cob_id_sdo_rx(self.node_id), ack))
            if last:
                index = self._seg_download_index
                subindex = self._seg_download_subindex
                if len(self._seg_download_buffer) != self._seg_download_total:
                    self._outbox.append(self._abort_frame(
                        index, subindex, 0x06070010))
                else:
                    entry = self.od.get((index, subindex))
                    if entry is not None:
                        entry.data = bytes(self._seg_download_buffer)
                self._seg_download_index = None
                self._seg_download_buffer = bytearray()
            return

    # -- 心跳调度 ----------------------------------------------------------

    def tick(self, now: float) -> None:
        """按 `heartbeat_period_ms` 排心跳。由 `MockBus` 在时间推进时调用。

        周期 0 表示不发心跳——这正是一个被 0x1017 关掉心跳的真实节点，
        节点扫描必须因此退化为 SDO 探测。
        """
        period = self.heartbeat_period_ms / 1000.0
        if period <= 0:
            return
        if now - self._last_heartbeat_at >= period:
            self._last_heartbeat_at = now
            self.queue_heartbeat()


# --------------------------------------------------------------------------
# 模拟总线
# --------------------------------------------------------------------------


class MockBus:
    """与 `canbus.CanBus` 同接口的模拟总线。

    帧的流转：`send()` 把帧交给所有挂载的 `MockNode.handle()`，节点把应答
    排进自己的 outbox；`recv()` 按顺序从各节点的 outbox 里取一帧返回。

    时间不是真实流逝的：`clock` 注入，`advance(dt)` 手工推进。
    `recv(timeout)` 的语义是"若虚拟时间窗口内没有帧就返回 None"——
    因此 `MockBus.silent()` 永远是 None，`MockBus(clock=...)` 可以精确控制
    心跳超时而不真的 sleep。
    """

    def __init__(self, nodes: Optional[Iterable[MockNode]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 loopback: bool = True) -> None:
        self.nodes: List[MockNode] = list(nodes or [])
        self.clock = clock or time.monotonic
        self.loopback = loopback
        self.closed = False
        self.tx_count = 0
        self.rx_count = 0
        self.sent: List[Frame] = []
        #: 收发配对记录 [(请求, 应答或 None), ...]，供测试逐帧比对语料。
        #: 这是"序列级"断言的基础：单帧比对看不出 toggle 交替与长度自洽。
        self.recorded_exchange: List[Tuple[Optional[Frame], Optional[Frame]]] = []
        self._pending_request: Optional[Frame] = None
        self._now = self.clock()
        #: 回放模式：录制序列。非空时 `send` 比对期望帧。
        self._replay: Optional["_ReplayCursor"] = None
        self.replay_mismatches: List[str] = []

    # -- 上下文协议 --------------------------------------------------------

    def open(self) -> "MockBus":
        self.closed = False
        return self

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "MockBus":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return not self.closed

    # -- 节点管理 ----------------------------------------------------------

    def add_node(self, node: MockNode) -> MockNode:
        self.nodes.append(node)
        return node

    def node(self, node_id: int) -> MockNode:
        for candidate in self.nodes:
            if candidate.node_id == node_id:
                return candidate
        raise KeyError("模拟总线上没有节点 %d" % node_id)

    # -- 时间 --------------------------------------------------------------

    def now(self) -> float:
        """当前虚拟时间。"""
        return self._now

    def advance(self, dt: float) -> List[Frame]:
        """推进虚拟时间 `dt` 秒，返回这段时间内各节点排出的自发帧。

        心跳就靠这里产生——`mockbus` 不跑后台线程，时间推进完全由调用方掌握。
        """
        if dt < 0:
            raise ValueError("dt 不能为负")
        target = self._now + dt
        # 周期心跳：把区间按每个节点的周期切分，逐拍排帧
        frames: List[Frame] = []
        for node in self.nodes:
            period = node.heartbeat_period_ms / 1000.0
            if period <= 0:
                continue
            # 从当前时刻起，每隔一个周期排一拍
            tick_at = self._now + period
            while tick_at <= target + 1e-9:
                node._last_heartbeat_at = tick_at
                frames.append(node.queue_heartbeat())
                tick_at += period
        self._now = target
        return frames

    # -- 收发 --------------------------------------------------------------

    def send(self, frame: Frame) -> None:
        if self.closed:
            raise MockBusError("总线已关闭")
        if frame.frame_type == FRAME_FD:
            # 与 canbus.CanBus 保持一致：FD 帧显式拒绝，绝不静默丢弃。
            from canbus import FdNotSupportedError
            raise FdNotSupportedError(
                "拒绝发送 CAN FD 帧到模拟总线（与真总线行为一致）")
        self.tx_count += 1
        self.sent.append(frame)
        # 一次只允许一个未配对请求：SDO 是严格的请求-应答式，
        # 若这里出现覆盖，说明上层在等应答时又发了新请求——那本身是缺陷。
        self._pending_request = frame
        if self._replay is not None:
            self._replay.check(frame, self)
        for node in self.nodes:
            node.handle(frame, self._now)
        if self.loopback:
            # 回环时自己的帧也会回到接收队列末尾；这里不模拟，
            # 因为那会污染 SDO 的收发配对。需要时用 canbus 的真回环测。
            pass

    def recv(self, timeout: Optional[float] = None) -> Optional[Frame]:
        if self.closed:
            raise MockBusError("总线已关闭")
        # 回放模式下优先给录制好的应答帧
        if self._replay is not None:
            replayed = self._replay.next_response()
            if replayed is not None:
                self.rx_count += 1
                self._record_exchange(replayed)
                return replayed
        for node in self.nodes:
            frame = node.pop_outbox()
            if frame is not None:
                self.rx_count += 1
                self._record_exchange(frame)
                return frame
        if timeout and timeout > 0:
            # 模拟等待：推进虚拟时间，期间可能产生心跳
            self.advance(timeout)
            for node in self.nodes:
                frame = node.pop_outbox()
                if frame is not None:
                    self.rx_count += 1
                    self._record_exchange(frame)
                    return frame
        # 超时：记一条 (请求, None)。"没有应答"本身是要被断言的序列内容——
        # 静默总线上的扫描就全靠这些 None 配对来验证。
        self._record_exchange(None)
        return None

    def _record_exchange(self, response: Optional[Frame]) -> None:
        self.recorded_exchange.append((self._pending_request, response))
        self._pending_request = None

    def drain(self, timeout: float = 0.0) -> List[Frame]:
        frames: List[Frame] = []
        while True:
            frame = self.recv(timeout)
            if frame is None:
                return frames
            frames.append(frame)

    # -- 录制/回放 ---------------------------------------------------------

    def recorder(self) -> "TraceRecorder":
        """开始录制。返回的 recorder 收集随后的请求/应答序列。"""
        return TraceRecorder(self)

    def load_replay(self, trace: Dict[str, object]) -> None:
        """进入回放模式：`send` 比对期望请求，`recv` 返回录制应答。"""
        self._replay = _ReplayCursor(trace, self)

    @classmethod
    def from_trace(cls, trace: Dict[str, object],
                   clock: Optional[Callable[[], float]] = None) -> "MockBus":
        """按一条录制好的序列构造回放总线。"""
        bus = cls(nodes=[], clock=clock)
        bus.load_replay(trace)
        return bus

    @classmethod
    def silent(cls, clock: Optional[Callable[[], float]] = None) -> "MockBus":
        """一条没有任何节点的总线——永远收不到帧。

        静默总线上的节点扫描是最容易写错的回归：实现若把"没应答"
        当成"节点存在"或直接抛异常，这里就会暴露。
        """
        return cls(nodes=[], clock=clock)

    def __repr__(self) -> str:
        return "MockBus(nodes=%s, tx=%d, rx=%d)" % (
            [n.node_id for n in self.nodes], self.tx_count, self.rx_count)


# --------------------------------------------------------------------------
# 帧 <-> JSON
# --------------------------------------------------------------------------


def frame_to_json(frame: Frame) -> Dict[str, object]:
    """帧 -> 可 JSON 化的 dict。金标语料与录制文件都用这个形状。"""
    result: Dict[str, object] = {
        "id": "0x%03X" % frame.arbitration_id,
        "data": " ".join("%02X" % b for b in frame.data),
    }
    if frame.extended:
        result["extended"] = True
    if frame.remote:
        result["remote"] = True
    if frame.frame_type != FRAME_CLASSIC:
        result["type"] = frame.frame_type
    return result


def frame_from_json(obj: Dict[str, object]) -> Frame:
    """JSON dict -> 帧。接受 `id` 是 "0x123" 或 291 两种写法。

    金标语料是手写的，两种写法都会出现；只认一种会让写语料的人反复踩坑。
    """
    raw_id = obj.get("id")
    if raw_id is None:
        raise ValueError("帧缺少 id 字段: %r" % (obj,))
    if isinstance(raw_id, str):
        arbitration_id = int(raw_id, 16) if raw_id.lower().startswith("0x") \
            else int(raw_id, 16) if not raw_id.isdigit() else int(raw_id)
    else:
        arbitration_id = int(raw_id)
    raw_data = obj.get("data", "")
    if isinstance(raw_data, str):
        cleaned = raw_data.replace(",", " ").strip()
        data = bytes(int(part, 16) for part in cleaned.split()) if cleaned else b""
    else:
        data = bytes(raw_data)  # type: ignore[arg-type]
    return Frame(
        arbitration_id=arbitration_id,
        data=data,
        extended=bool(obj.get("extended", False)),
        remote=bool(obj.get("remote", False)),
        frame_type=str(obj.get("type", FRAME_CLASSIC)),
    )


# --------------------------------------------------------------------------
# 录制
# --------------------------------------------------------------------------


class TraceRecorder:
    """录制一串请求/应答。产出 `{"name":..., "steps":[{"request":..., "response":...}]}`。

    形状与金标语料一致——这样现场录下来的故障序列可以直接变成回归条目，
    不需要再手工抄一遍帧。
    """

    def __init__(self, bus: Optional[MockBus] = None, name: str = "") -> None:
        self.bus = bus
        self.name = name
        self.steps: List[Dict[str, object]] = []
        self._pending_request: Optional[Frame] = None

    def record_request(self, frame: Frame) -> None:
        self._pending_request = frame

    def record_response(self, frame: Optional[Frame]) -> None:
        entry: Dict[str, object] = {}
        if self._pending_request is not None:
            entry["request"] = frame_to_json(self._pending_request)
        entry["response"] = None if frame is None else frame_to_json(frame)
        self.steps.append(entry)
        self._pending_request = None

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "steps": list(self.steps)}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path: str, indent: int = 2) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json(indent=indent))
            fh.write("\n")


class _ReplayCursor:
    """回放游标：按 steps 顺序比对请求、给出应答。"""

    def __init__(self, trace: Dict[str, object], bus: MockBus) -> None:
        raw_steps = trace.get("steps")
        if not isinstance(raw_steps, list):
            raise MockBusError("录制文件缺少 steps 数组")
        self.steps: List[Dict[str, object]] = raw_steps  # type: ignore[assignment]
        self.bus = bus
        self.index = 0
        self.mismatches: List[str] = []

    def check(self, frame: Frame, bus: MockBus) -> None:
        if self.index >= len(self.steps):
            return  # 超出录制范围：不再比对，让上层自己处理
        step = self.steps[self.index]
        raw_request = step.get("request")
        if raw_request is None:
            return
        expected = frame_from_json(raw_request)  # type: ignore[arg-type]
        if expected != frame:
            message = "回放第 %d 步请求不符: 期望 %s，实际 %s" % (
                self.index, expected, frame)
            self.mismatches.append(message)
            bus.replay_mismatches.append(message)
            raise TraceMismatchError(message)

    def next_response(self) -> Optional[Frame]:
        if self.index >= len(self.steps):
            return None
        step = self.steps[self.index]
        self.index += 1
        raw_response = step.get("response")
        if raw_response is None:
            return None
        return frame_from_json(raw_response)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 便捷构造
# --------------------------------------------------------------------------


def make_sdo_golden_node(node_id: int = 1, **kwargs) -> MockNode:
    """造一个够金标语料用的标准节点。

    只挂规范公共对象 + 两个用于分段传输的长对象（厂家名称类），
    **不含任何厂家私有索引**——与 `canopen.py` 的硬规则一致。
    """
    long_payload = bytes(range(0x40))            # 64 字节，触发分段上传
    short_string = b"km3-hwtui"                  # 9 字节，触发分段上传（短）
    od = {
        (0x1008, 0): OdEntry(long_payload),
        (0x1009, 0): OdEntry(b"rev-A"),
        (0x100A, 0): OdEntry(short_string),
        # 一个可写的 2 字节公共对象：心跳周期
        (0x1017, 0): OdEntry(b"\x00\x00", writable=True),
        # 一个可写的 4 字节对象：通讯周期
        (0x1006, 0): OdEntry(b"\x00\x00\x00\x00", writable=True),
        # 一个只读的 1 字节对象：错误寄存器
        (0x1001, 0): OdEntry(b"\x00"),
        # PDO 映射与通讯参数（只读的样例，供 read_pdo_* 回归）
        (0x1600, 0): OdEntry(b"\x02"),
        (0x1600, 1): OdEntry((0x6040 | (0 << 16) | (16 << 24)).to_bytes(4, "little")),
        (0x1600, 2): OdEntry((0x6060 | (0 << 16) | (8 << 24)).to_bytes(4, "little")),
        (0x1400, 0): OdEntry(b"\x02"),
        (0x1400, 1): OdEntry((0x200 + node_id).to_bytes(4, "little")),
        (0x1400, 2): OdEntry(b"\xff"),
    }
    merged = dict(od)
    if "od" in kwargs and kwargs["od"]:
        merged.update(kwargs.pop("od"))
    return MockNode(node_id, od=merged, **kwargs)
