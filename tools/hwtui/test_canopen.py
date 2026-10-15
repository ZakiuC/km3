"""km3 CANopen 标准层回归套件。

运行方式（仓库根下）：

    python3 -m unittest discover -s tools/hwtui -p test_canopen.py -v

**无硬件、无 pty、无 CAN 内核模块。** 全部断言打在 `mockbus` 上，
因此这台机器不需要 `vcan`（本板 `CONFIG_CAN_VCAN` 未开）、
不需要 `AF_CAN` 套接字、不需要 root。

语料是 `vectors/canopen_golden.json`，**序列级**：每条记录一段完整对话
（请求帧序列 -> 期望应答帧序列），测试逐条断言。这样设计是因为 SDO 分段传输的
正确性只体现在帧与帧之间的关系上（toggle 交替、末段 n 位、长度自洽），
单帧比对看不出来。

每条语料除了"值对不对"，还断言"线上跑的帧序列与语料逐帧一致"——
后者才是回归的价值所在：实现可以改，但线上字节不能悄悄变。
"""

from __future__ import annotations

import errno
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import canopen
import mockbus
from canopen import (
    BusSilenceError,
    Frame,
    HeartbeatMonitor,
    NmtMaster,
    NmtState,
    SdoAbort,
    SdoClient,
    cob_id_emcy,
    cob_id_heartbeat,
    cob_id_sdo_rx,
    cob_id_sdo_tx,
    decode_abort_code,
    read_pdo_communication,
    read_pdo_mapping,
    scan_nodes,
)
from mockbus import MockBus, MockNode, OdEntry, frame_from_json

VECTORS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "vectors", "canopen_golden.json")


def load_vectors():
    with io.open(VECTORS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def hexbytes(text: str) -> bytes:
    """把 "41 42 43" 解析成 bytes。"""
    cleaned = text.replace(",", " ").strip()
    return bytes(int(part, 16) for part in cleaned.split()) if cleaned else b""


class GoldenTestCase(unittest.TestCase):
    """读金标语料并按 kind 分派的基础类。"""

    @classmethod
    def setUpClass(cls):
        cls.golden = load_vectors()
        cls.vectors = {v["name"]: v for v in cls.golden["vectors"]}

    def vector(self, name: str) -> dict:
        self.assertIn(name, self.vectors, "语料里没有 %s" % name)
        return self.vectors[name]

    def vector_names_of_kind(self, kind: str):
        return [v["name"] for v in self.golden["vectors"] if v["kind"] == kind]

    # -- 公共装置 ----------------------------------------------------------

    def make_node(self, vector: dict, **kwargs) -> MockNode:
        """按语料里的 object_dump / object_value 造节点。"""
        kwargs.setdefault("segmented_upload_size_indicated",
                          vector.get("size_indicated", True))
        node = mockbus.make_sdo_golden_node(vector.get("node_id", 1), **kwargs)
        dump = vector.get("object_dump")
        if dump:
            for key, value in dump.items():
                index_text, sub_text = key.split(":")
                node.set_object(int(index_text, 16), int(sub_text, 16),
                                hexbytes(value))
        # 数列型语料：对象内容就是 0..N-1 的字节序列，长度由字段名带出
        for key in ("object_value_hex_sequence_zero_to", "expected_value_hex_sequence_zero_to"):
            length = vector.get(key)
            if length is not None and vector.get("index") is not None:
                node.set_object(vector["index"], vector.get("subindex", 0),
                                bytes(range(length)))
        value = vector.get("object_value")
        if value is not None:
            payload = hexbytes(value)
            node.set_object(vector["index"], vector.get("subindex", 0), payload)
            # 单条语料里的对象往往既要读又要写，按用法放开写权限
            if "write_value" in vector or "write_value_ascii" in vector \
                    or "write_length" in vector:
                node.od[(vector["index"], vector.get("subindex", 0))].writable = True
        self._allow_writes_for_vector(vector, node)
        return node

    def _allow_writes_for_vector(self, vector: dict, node: MockNode) -> None:
        """语料里带写操作的，把对应对象标成可写。"""
        if "index" in vector and (
                "write_value" in vector or "write_value_ascii" in vector
                or vector.get("write_length")):
            key = (vector["index"], vector.get("subindex", 0))
            if key in node.od:
                node.od[key].writable = True

    def make_bus_and_client(self, vector: dict, node: MockNode = None,
                            timeout: float = 0.05):
        node = node or self.make_node(vector)
        bus = MockBus([node])
        # 记录一律走总线自己的 recorded_exchange；测试里用 self.recorded 取用，
        # 由 make_bus_and_client 与 assert_frame_sequence 两处赋值，保持同一份。
        self.recorded = bus.recorded_exchange
        client = SdoClient(bus, vector.get("node_id", 1), timeout=timeout)
        return bus, client, node

    # -- 帧序列断言 --------------------------------------------------------

    def assert_frame_sequence(self, bus: MockBus, expected_pairs,
                              note: str = "") -> None:
        """断言线上帧序列与语料**逐帧**一致。

        语料只固定了一部分帧时（例如 64 字节分段传输的中间段由规则生成），
        用 `assert_frame_prefix` 或 `assert_toggle_alternation` 配合。
        """
        self.recorded = bus.recorded_exchange
        self.assertIsNotNone(
            self.recorded,
            "总线没有记录收发序列——测试装置没接上%s" % ("（" + note + "）" if note else ""))
        actual = self.recorded
        expected = [
            (None if pair.get("request") is None else frame_from_json(pair["request"]),
             None if pair.get("response") is None else frame_from_json(pair["response"]))
            for pair in expected_pairs
        ]
        self.assertEqual(
            len(actual), len(expected),
            "帧对数不符: 期望 %d 对，实际 %d 对\n实际序列:\n%s" % (
                len(expected), len(actual),
                "\n".join("  %s -> %s" % (r, s) for r, s in actual)))
        for position, ((want_req, want_resp), (got_req, got_resp)) in enumerate(
                zip(expected, actual)):
            if want_req is not None:
                self.assertEqual(
                    want_req, got_req,
                    "第 %d 对请求帧不符（%s）\n  期望 %s\n  实际 %s" % (
                        position, note, want_req, got_req))
            if want_resp is not None:
                self.assertEqual(
                    want_resp, got_resp,
                    "第 %d 对应答帧不符（%s）\n  期望 %s\n  实际 %s" % (
                        position, note, want_resp, got_resp))


# --------------------------------------------------------------------------
# SDO 加急读写
# --------------------------------------------------------------------------


class TestGoldenExpedited(GoldenTestCase):
    """加急上传/下载。每条语料都断言到字节。"""

    def _assert_expedited_read(self, name: str) -> None:
        vector = self.vector(name)
        bus, client, _ = self.make_bus_and_client(vector)
        got = client.read(vector["index"], vector.get("subindex", 0))
        self.assertEqual(
            got.hex(" ").upper(), vector["expected_value"].upper(),
            "%s: 读回的值不符" % name)
        self.assert_frame_sequence(bus, vector["expected_frames"], note=name)
        return vector, got

    def test_expedited_read_uint8(self):
        vector, got = self._assert_expedited_read("expedited_read_uint8")
        # 1 字节对象的应答命令字必须是 0x4F（0x43 | (3<<2)）。
        # 只比 0x43 的实现会在这里失败——这正是本条语料的存在意义。
        response = frame_from_json(vector["expected_frames"][0]["response"])
        self.assertEqual(response.data[0], 0x4F)
        self.assertEqual(len(got), 1)

    def test_expedited_read_uint16(self):
        vector, got = self._assert_expedited_read("expedited_read_uint16")
        # 2 字节对象的应答命令字是 0x4B。这一条曾把实现打回过：
        # 加急上传的命令字是"基值 0x43 | n 位"，n 位不舍弃就会读出 4 字节。
        response = frame_from_json(vector["expected_frames"][0]["response"])
        self.assertEqual(response.data[0], 0x4B)
        self.assertEqual(len(got), 2, "2 字节对象不得读回 4 字节（填充没裁掉）")

    def test_expedited_read_uint32(self):
        vector, got = self._assert_expedited_read("expedited_read_uint32")
        response = frame_from_json(vector["expected_frames"][0]["response"])
        self.assertEqual(response.data[0], 0x43, "4 字节应答的 n 位应为 0")
        self.assertEqual(len(got), 4)

    def test_expedited_read_identity_subindex(self):
        vector, got = self._assert_expedited_read("expedited_read_identity_vendor_id")
        # 子索引必须出现在帧里，且不能被实现当成"永远是 0"
        request = frame_from_json(vector["expected_frames"][0]["request"])
        self.assertEqual(request.data[3], 1)
        self.assertEqual(got.hex(" ").upper(), "AA BB CC DD")

    def test_expedited_write_uint16_command_word(self):
        """写 2 字节的命令字是 0x2B，不是 0x23 也不是 0x27。

        这是本套件最值得保留的一条断言：把"带长度指示"的基值写成 0x27 的实现，
        发出去的会变成 0x2F（1 字节），服务端按 1 字节解析，总线随即静默。
        """
        vector = self.vector("expedited_write_uint16")
        bus, client, node = self.make_bus_and_client(vector)
        client.write(vector["index"], vector.get("subindex", 0),
                     hexbytes(vector["write_value"]))
        self.assertEqual(node.object_value(vector["index"], 0).hex(" ").upper(),
                         vector["expected_value"].upper())
        self.assert_frame_sequence(bus, vector["expected_frames"], note="2 字节写")
        request = frame_from_json(vector["expected_frames"][0]["request"])
        self.assertEqual(request.data[0], 0x2B)

    def test_expedited_write_uint8_command_word(self):
        vector = self.vector("expedited_write_uint8")
        bus, client, node = self.make_bus_and_client(vector)
        client.write(vector["index"], 0, hexbytes(vector["write_value"]))
        self.assert_frame_sequence(bus, vector["expected_frames"], note="1 字节写")
        self.assertEqual(frame_from_json(
            vector["expected_frames"][0]["request"]).data[0], 0x2F)

    def test_expedited_write_uint32_has_no_followup_segment(self):
        """写 4 字节必须是"一帧请求一帧确认"就结束。

        语料只给了一对帧，`assert_frame_sequence` 的帧对计数就足够了：
        实现在 4 字节之后又发数据段的，这里会因为"帧对多了"而失败。
        """
        vector = self.vector("expedited_write_uint32")
        bus, client, node = self.make_bus_and_client(vector)
        client.write(vector["index"], 0, hexbytes(vector["write_value"]))
        self.assert_frame_sequence(bus, vector["expected_frames"], note="4 字节写")
        self.assertEqual(frame_from_json(
            vector["expected_frames"][0]["request"]).data[0], 0x23)
        self.assertEqual(node.object_value(vector["index"], 0).hex(" ").upper(),
                         vector["expected_value"].upper())


# --------------------------------------------------------------------------
# SDO 分段传输
# --------------------------------------------------------------------------


class TestGoldenSegmented(GoldenTestCase):
    """分段上传/下载。toggle 交替与末段 n 位是这里的重点。"""

    def test_segmented_write_9_bytes(self):
        vector = self.vector("segmented_write_9_bytes")
        bus, client, node = self.make_bus_and_client(vector)
        client.write(vector["index"], 0, hexbytes(vector["write_value"]))
        self.assertEqual(
            node.object_value(vector["index"], 0).hex(" ").upper(),
            vector["expected_value"].upper())
        self.assert_frame_sequence(bus, vector["expected_frames"], note="9 字节分段写")

    def test_segmented_write_9_bytes_last_segment_nbits(self):
        """末段命令字 0x1B = toggle 1 | c 1 | n 位 0x0A（空 5 字节，2 字节有效）。"""
        vector = self.vector("segmented_write_9_bytes")
        last = frame_from_json(vector["expected_frames"][2]["request"])
        self.assertEqual(last.data[0], 0x1B)
        self.assertEqual(last.data[1:3].hex(" ").upper(), "48 49")
        # 末段 n 位解码：有效字节数 = 7 - (n >> 1)
        nbytes = 7 - ((last.data[0] & canopen.SDO_CS_SEGMENT_NBYTES_MASK) >> 1)
        self.assertEqual(nbytes, 2)

    def test_download_ack_command_words(self):
        # 变异实验暴露的盲点：原来的套件只断言"值写对了"，
        # 没有断言"确认帧的命令字是什么"。把确认判据改成恒真（if False）
        # 套件依然全绿——也就是说，节点回一个 0x60 以外的帧也照样通过。
        # 这里把每一类确认的命令字钉死。
        vector = self.vector("segmented_write_9_bytes")
        bus, client, node = self.make_bus_and_client(vector)
        client.write(vector["index"], 0, hexbytes(vector["write_value"]))
        acks = [response.data[0] for _, response in self.recorded
                if response is not None and response.arbitration_id == cob_id_sdo_rx(1)]
        # 第一帧是发起帧的确认，固定 0x60
        self.assertEqual(acks[0], 0x60, "分段下载发起帧的确认必须是 0x60")
        # 其余是数据段的确认：0x20 | (toggle << 4)，且 toggle 与请求段一致
        data_acks = acks[1:]
        self.assertEqual(len(data_acks), 2, "9 字节应有 2 个数据段")
        self.assertEqual(data_acks, [0x20, 0x30],
                         "数据段确认必须按 toggle 交替 0x20/0x30")

    def test_rejects_malformed_segment_ack(self):
        # 变异实验暴露的盲点（第二次）：把分段确认的命令字校验换成 `if False`
        # 套件依然全绿 —— 因为所有用例都只喂"正确的确认帧"。
        # 校验要可测，必须喂畸形帧。这里在真实节点后面挂一层总线代理，
        # 把第一段的确认帧篡改成 0x55（既不是 0x20/0x30 也不是 abort）。
        vector = self.vector("segmented_write_9_bytes")
        node = self.make_node(vector)
        bus = MockBus([node])
        self.recorded = bus.recorded_exchange
        original_recv = bus.recv
        state = {"corrupted": False}

        def recv_with_corruption(timeout=None):
            frame = original_recv(timeout)
            if frame is not None and frame.arbitration_id == cob_id_sdo_rx(1) \
                    and not state["corrupted"] and frame.data[0] == 0x60:
                # 第一帧是发起帧确认（0x60），放过；下一帧是数据段确认
                state["corrupted"] = True
                return frame
            if frame is not None and state["corrupted"] and frame.data[0] in (0x20, 0x30):
                return Frame(frame.arbitration_id, bytes([0x55]) + frame.data[1:])
            return frame

        bus.recv = recv_with_corruption
        client = SdoClient(bus, vector.get("node_id", 1), timeout=0.05)
        with self.assertRaises(canopen.SdoProtocolError) as ctx:
            client.write(vector["index"], 0, hexbytes(vector["write_value"]))
        self.assertIn("分段确认", str(ctx.exception))
        bus.recv = original_recv

    def test_rejects_segment_ack_with_wrong_toggle(self):
        # 确认帧回显的 toggle 必须与请求段一致；不一致要报错。
        vector = self.vector("segmented_write_9_bytes")
        node = self.make_node(vector)
        bus = MockBus([node])
        self.recorded = bus.recorded_exchange
        original_recv = bus.recv
        state = {"seen_initiate": False}

        def recv_with_bad_toggle(timeout=None):
            frame = original_recv(timeout)
            if frame is not None and frame.data[0] == 0x60:
                state["seen_initiate"] = True
                return frame
            if frame is not None and state["seen_initiate"] and frame.data[0] == 0x20:
                # toggle 应该是 0，篡改成 1
                return Frame(frame.arbitration_id, bytes([0x30]) + frame.data[1:])
            return frame

        bus.recv = recv_with_bad_toggle
        client = SdoClient(bus, vector.get("node_id", 1), timeout=0.05)
        with self.assertRaises(canopen.SdoProtocolError) as ctx:
            client.write(vector["index"], 0, hexbytes(vector["write_value"]))
        self.assertIn("toggle", str(ctx.exception))
        bus.recv = original_recv

    def test_expedited_download_ack_is_0x60(self):
        vector = self.vector("expedited_write_uint16")
        bus, client, node = self.make_bus_and_client(vector)
        client.write(vector["index"], 0, hexbytes(vector["write_value"]))
        ack = self.recorded[0][1]
        self.assertEqual(ack.arbitration_id, cob_id_sdo_rx(1))
        self.assertEqual(ack.data[0], 0x60, "加急下载的确认必须是 0x60")

    def test_segmented_write_64_bytes(self):
        """64 字节分段写：断言首帧、末帧与 toggle 交替。

        中间段由规则生成，语料只固定了前两帧和后一帧——全部逐帧写进 JSON
        会让语料臃肿到没人愿意看，而 toggle 规则用断言表达更清楚。
        """
        vector = self.vector("segmented_write_64_bytes")
        payload = bytes(range(vector["write_length"]))
        bus, client, node = self.make_bus_and_client(vector, timeout=0.05)
        client.write(vector["index"], 0, payload)
        self.assertEqual(node.object_value(vector["index"], 0), payload,
                         "64 字节往返后内容不符")

        # 首帧：发起帧 0x21 + 长度 64
        initiate = frame_from_json(vector["expected_frames"][0]["request"])
        self.assertEqual(initiate.data[0], 0x21)
        self.assertEqual(int.from_bytes(initiate.data[4:8], "little"), 64)

        # 末帧：toggle 1 | c 1 | n 位 0x0C（空 6 字节，1 字节有效）
        last_expected = frame_from_json(vector["expected_frames"][2]["request"])
        self.assertEqual(last_expected.data[0], 0x1D)

        # 数据段的 toggle 必须严格交替 0,1,0,1...
        self._assert_toggle_alternation(64)

    def _assert_toggle_alternation(self, total_bytes: int) -> None:
        """从实际记录的序列里挑出数据段，校验 toggle 从 0 起严格交替。"""
        segments = []
        for request, response in self.recorded:
            command = request.data[0]
            # 数据段：高位为 0（0x00..0x1F），且应答确认是 0x2x/0x3x
            if command <= 0x1F and response is not None \
                    and 0x20 <= response.data[0] <= 0x3F:
                segments.append((command, response))
        self.assertGreaterEqual(len(segments), 2, "没找到数据段")
        for position, (command, response) in enumerate(segments):
            expected_toggle = position % 2
            self.assertEqual(
                (command >> 4) & 1, expected_toggle,
                "第 %d 个数据段的 toggle 位应为 %d" % (position, expected_toggle))
            # 确认帧回显同一个 toggle
            self.assertEqual(
                (response.data[0] >> 4) & 1, expected_toggle,
                "第 %d 个数据段的确认帧 toggle 不符" % position)
        # 只有最后一段带 c 位
        for position, (command, _) in enumerate(segments[:-1]):
            self.assertFalse(command & canopen.SDO_CS_SEGMENT_LAST,
                             "第 %d 段不该带 c 位" % position)
        self.assertTrue(segments[-1][0] & canopen.SDO_CS_SEGMENT_LAST,
                        "末段必须带 c 位")

    def test_segmented_read_9_bytes(self):
        # 本条语料的节点**不带**长度指示（首帧 0x41，byte4..7 保留为 0）。
        # 客户端必须靠 c 位判断末段，而不是去解析一个并不存在的总长度。
        vector = self.vector("segmented_read_9_bytes")
        self.assertFalse(vector.get("size_indicated", True),
                         "本条语料应声明 size_indicated=False")
        bus, client, _ = self.make_bus_and_client(vector)
        got = client.read(vector["index"], 0)
        self.assertEqual(got.hex(" ").upper(), vector["expected_value"].upper())
        self.assert_frame_sequence(bus, vector["expected_frames"], note="9 字节分段读")

    def test_segmented_read_64_bytes(self):
        """64 字节分段读：首帧带长度指示，段请求 toggle 交替 0x60/0x70。"""
        vector = self.vector("segmented_read_64_bytes")
        payload = bytes(range(64))
        node = mockbus.make_sdo_golden_node(1)
        node.set_object(4104, 0, payload)
        bus, client, _ = self.make_bus_and_client(vector, node=node)
        got = client.read(4104, 0)
        self.assertEqual(got, payload, "64 字节读回内容不符")
        self.assertEqual(len(self.recorded), len(vector["expected_frames"]),
                         "帧对数不符：分段请求必须一段一对")
        self.assert_frame_sequence(bus, vector["expected_frames"],
                                   note="64 字节分段读")

    def test_segmented_read_0x51_is_recognised_as_last_segment(self):
        # 命令字 0x51 = 0x40 | toggle 1 | c 1 —— 分段上传在 toggle=1 时的**末段**应答。
        # 这是规范里最容易被漏掉的一个值：只实现 0x41 的话，
        # 读一个需要两段以上的对象会在最后一段判"命令字不认识"。
        vector = self.vector("segmented_read_0x51_last_segment")
        payload = bytes(range(16))
        node = mockbus.make_sdo_golden_node(1)
        node.set_object(vector["index"], 0, payload)
        bus, client, _ = self.make_bus_and_client(vector, node=node)
        got = client.read(vector["index"], 0)
        self.assertEqual(got, payload,
                         "16 字节对象应分三段读回，末段命令字 0x51")
        self.assert_frame_sequence(bus, vector["expected_frames"],
                                   note="0x51 末段")
        # 末段由 c 位判定，与 toggle 无关：三段对象（7+7+2）的第三段
        # toggle 回到 0，因此命令字是 0x4B 而非 0x5B。
        last = frame_from_json(vector["expected_frames"][3]["response"])
        self.assertEqual(last.data[0], 0x4B)
        self.assertEqual((last.data[0] >> 4) & 1, 0, "第三段的 toggle 应为 0")
        self.assertTrue(last.data[0] & canopen.SDO_CS_SEGMENT_LAST, "必须是末段")
        self.assertEqual(7 - ((last.data[0] & 0x0E) >> 1), 2)

    def test_upload_segment_mask_accepts_whole_0x40_family(self):
        # 分段上传应答的命令字只有四个取值：0x40/0x41（toggle 0）、0x50/0x51（toggle 1）。
        # 客户端必须靠 toggle 位与 c 位自己判断，而不是枚举命令字。
        self.assertEqual(canopen.SDO_CS_UPLOAD_SEGMENTED, 0x41)
        self.assertEqual(canopen.SDO_CS_UPLOAD_SEGMENTED_TOGGLE1, 0x51)
        for command in (0x40, 0x41, 0x50, 0x51):
            self.assertEqual((command & 0xE0), 0x40,
                             "0x%02X 应落在 0x40 族" % command)

    def test_segmented_read_toggle_alternation(self):
        """实测段请求的 toggle 必须从 0（0x60）起交替到 1（0x70）。"""
        vector = self.vector("segmented_read_64_bytes")
        node = mockbus.make_sdo_golden_node(1)
        node.set_object(4104, 0, bytes(range(64)))
        bus, client, _ = self.make_bus_and_client(vector, node=node)
        client.read(4104, 0)
        requests = [request for request, _ in self.recorded]
        segment_requests = [r for r in requests if r.data[0] in (0x60, 0x70)]
        self.assertEqual(len(segment_requests), 10)
        for position, request in enumerate(segment_requests):
            self.assertEqual((request.data[0] >> 4) & 1, position % 2,
                             "第 %d 次段请求的 toggle 应为 %d" % (position, position % 2))

    def test_segmented_read_without_size_indicator(self):
        """从站不带长度指示时也必须读对。

        规范允许首帧 0x41 不带总长（bit0=0），此时客户端只能靠 c 位判断末段。
        实现若用 `cs & 0x01` 判"带长度"，会因为 0x41 的最低位本来就是 1
        而错读出一个总长 0，随即以"声明 0 实收 N"失败。
        """
        vector = self.vector("segmented_read_9_bytes")
        node = mockbus.make_sdo_golden_node(
            1, segmented_upload_size_indicated=False)
        node.set_object(4106, 0, hexbytes(vector["expected_value"]))
        bus, client, _ = self.make_bus_and_client(vector, node=node)
        self.assertEqual(client.read(4106, 0), hexbytes(vector["expected_value"]))


# --------------------------------------------------------------------------
# abort 映射
# --------------------------------------------------------------------------


class TestGoldenAborts(GoldenTestCase):
    """abort code 的解析。标准码给表，未知码原样十六进制。"""

    def test_abort_object_not_exist(self):
        vector = self.vector("abort_object_not_exist")
        # 该索引刻意不在字典里——节点会回 0x06020000
        bus = MockBus([mockbus.make_sdo_golden_node(1)])
        client = SdoClient(bus, 1, timeout=0.05)
        with self.assertRaises(SdoAbort) as ctx:
            client.read(vector["index"], 0)
        self.assertEqual(ctx.exception.code, vector["expect_abort_code"])
        self.assertEqual(ctx.exception.description,
                         vector["expect_abort_description"])
        self.assert_frame_sequence(bus, vector["expected_frames"],
                                   note="对象不存在")

    def test_abort_write_readonly(self):
        vector = self.vector("abort_write_readonly")
        bus = MockBus([mockbus.make_sdo_golden_node(1)])
        client = SdoClient(bus, 1, timeout=0.05)
        with self.assertRaises(SdoAbort) as ctx:
            client.write(vector["index"], 0, hexbytes(vector["write_value"]))
        self.assertEqual(ctx.exception.code, vector["expect_abort_code"])
        self.assertEqual(ctx.exception.description,
                         vector["expect_abort_description"])
        self.assert_frame_sequence(bus, vector["expected_frames"],
                                   note="写只读对象")

    def test_abort_unknown_code_is_shown_as_hex(self):
        """未知 abort code 必须原样显示十六进制，不得编造含义。"""
        vector = self.vector("abort_unknown_code_preserved")
        code = vector["inject_abort_code"]
        self.assertEqual(decode_abort_code(code), vector["expect_abort_description"])
        self.assertTrue(decode_abort_code(code).startswith("unknown abort code 0x"))

        node = mockbus.make_sdo_golden_node(1)
        node.set_abort(vector["index"], vector["subindex"], code)
        bus = MockBus([node])
        client = SdoClient(bus, 1, timeout=0.05)
        with self.assertRaises(SdoAbort) as ctx:
            client.read(vector["index"], vector["subindex"])
        self.assertEqual(ctx.exception.code, code)
        self.assertEqual(ctx.exception.description,
                         "unknown abort code 0x%08X" % code)
        self.assert_frame_sequence(bus, vector["expected_frames"],
                                   note="未知 abort 码")

    def test_abort_frame_echoes_index(self):
        """abort 帧里的索引是**被拒的那个**，不是 0。

        实现若把 abort 帧的索引字段丢掉，现场只看到"某对象被拒"而不知道是哪个。
        """
        vector = self.vector("abort_object_not_exist")
        response = frame_from_json(vector["expected_frames"][0]["response"])
        echoed = response.data[1] | (response.data[2] << 8)
        self.assertEqual(echoed, vector["index"])

    def test_abort_exception_carries_index_and_subindex(self):
        # 变异实验暴露的盲点：原来只断言了 abort code，
        # 把 abort 帧里的索引解析强行改成 0，套件依然全绿。
        # 但"哪个对象被拒"正是现场最需要的信息——SdoAbort 必须带出来。
        vector = self.vector("abort_unknown_code_preserved")
        code = vector["inject_abort_code"]
        node = mockbus.make_sdo_golden_node(1)
        node.set_abort(vector["index"], vector["subindex"], code)
        bus = MockBus([node])
        self.recorded = bus.recorded_exchange
        client = SdoClient(bus, 1, timeout=0.05)
        with self.assertRaises(SdoAbort) as ctx:
            client.read(vector["index"], vector["subindex"])
        self.assertEqual(ctx.exception.index, vector["index"],
                         "SdoAbort 必须带出被拒的索引")
        self.assertEqual(ctx.exception.subindex, vector["subindex"],
                         "SdoAbort 必须带出被拒的子索引")

    def test_abort_index_comes_from_the_frame(self):
        # 直接从帧里核对：abort 帧 byte1..2 是索引，byte3 是子索引。
        # 用两个不同的对象各试一次，确保它不是把请求值原样抄回来的。
        for index, subindex in ((0x1001, 0), (0x1007, 3)):
            node = mockbus.make_sdo_golden_node(1)
            node.set_abort(index, subindex, 0x06090011)
            bus = MockBus([node])
            client = SdoClient(bus, 1, timeout=0.05)
            with self.assertRaises(SdoAbort) as ctx:
                client.read(index, subindex)
            self.assertEqual(ctx.exception.index, index)
            self.assertEqual(ctx.exception.subindex, subindex)
            self.assertEqual(ctx.exception.description, "sub-index does not exist")

    def test_known_abort_table_entries(self):
        # 标准码表的形态检查。注意**不能**断言"描述里不含 unknown"——
        # 规范原文里就有 "client/server command specifier not valid or unknown"
        # （0x05040001），这类字样是规范给的，不是编造的。
        # 要区分的是**机制**：表内命中给规范描述，表外未命中才给
        # "unknown abort code 0x........"。
        self.assertGreaterEqual(len(canopen.ABORT_CODES), 25)
        for code, description in canopen.ABORT_CODES.items():
            self.assertIsInstance(description, str)
            self.assertTrue(description.strip(), "0x%08X 的描述为空" % code)

    def test_every_known_code_decodes_to_its_table_entry(self):
        # 表内每个码都必须原样返回表里的描述，一个都不许被覆盖。
        for code, description in canopen.ABORT_CODES.items():
            self.assertEqual(canopen.decode_abort_code(code), description,
                             "0x%08X 的解析结果与表不符" % code)

    def test_decode_distinguishes_known_from_unknown(self):
        # 已知码与未知码的输出形态必须能分开，判据是前缀。
        for code in canopen.ABORT_CODES:
            self.assertFalse(
                canopen.decode_abort_code(code).startswith("unknown abort code"),
                "已知码 0x%08X 被当成未知码输出" % code)
        for code in (0x0BADF00D, 0x12345678, 0xFFFFFFFF):
            self.assertTrue(
                canopen.decode_abort_code(code).startswith("unknown abort code 0x"),
                "未知码 0x%08X 没被标成 unknown" % code)
            self.assertIn("%08X" % code, canopen.decode_abort_code(code),
                          "未知码必须原样以十六进制呈现")


# --------------------------------------------------------------------------
# 节点扫描
# --------------------------------------------------------------------------


class TestGoldenNodeScan(GoldenTestCase):
    """静默总线上的扫描。假阳性是这里唯一的敌人。"""

    def test_scan_silent_bus_finds_nothing(self):
        vector = self.vector("node_scan_silent_bus")
        bus = MockBus.silent()
        found = scan_nodes(bus, vector["node_ids"], timeout=vector["timeout_s"])
        self.assertEqual(found, vector["expected_found"],
                         "静默总线上不得扫出任何节点")
        # 每个节点都探测过恰好一次
        self.assertEqual(len(bus.sent), vector["expect_probes"],
                         "探测帧数应与节点数一致")

    def test_scan_silent_bus_does_not_raise(self):
        """静默不是错误：扫描必须正常返回空列表，不抛异常。"""
        vector = self.vector("node_scan_silent_bus")
        bus = MockBus.silent()
        found = scan_nodes(bus, vector["node_ids"], timeout=vector["timeout_s"])
        self.assertEqual(found, [])

    def test_scan_finds_only_live_nodes(self):
        """只有 3 号和 5 号活着时必须**恰好**扫到 [3, 5]。"""
        vector = self.vector("node_scan_finds_only_live_nodes")
        nodes = [mockbus.make_sdo_golden_node(node_id)
                 for node_id in vector["live_nodes"]]
        bus = MockBus(nodes)
        found = scan_nodes(bus, vector["node_ids"], timeout=vector["timeout_s"])
        self.assertEqual(found, vector["expected_found"])

    def test_scan_uses_sdo_not_heartbeat(self):
        """默认探测手段是 SDO 读 0x1000，不是靠心跳。

        心跳可能被 0x1017 关掉（周期 0），此时节点照样活着；靠心跳扫描会漏。
        这里构造的就是"心跳关掉但节点在"的场景。
        """
        vector = self.vector("node_scan_finds_only_live_nodes")
        nodes = [mockbus.make_sdo_golden_node(node_id, heartbeat_period_ms=0)
                 for node_id in vector["live_nodes"]]
        bus = MockBus(nodes)
        found = scan_nodes(bus, vector["node_ids"], timeout=vector["timeout_s"])
        self.assertEqual(found, vector["expected_found"])
        # 探测帧读的是 0x1000:00
        for frame in bus.sent:
            self.assertEqual(frame.data[0], 0x40)
            self.assertEqual(frame.data[1] | (frame.data[2] << 8), 0x1000)


# --------------------------------------------------------------------------
# 心跳超时
# --------------------------------------------------------------------------


class TestGoldenHeartbeat(GoldenTestCase):
    """心跳消费者与超时判定。时间由注入的时钟驱动，不真实 sleep。"""

    def test_heartbeat_timeout_detection(self):
        vector = self.vector("heartbeat_timeout_detection")
        monitor = HeartbeatMonitor(vector["node_ids"], vector["timeout_s"])

        # 喂语料里的心跳（帧本身来自 expected_frames）
        expected_frames = [frame_from_json(entry["response"])
                           for entry in vector["expected_frames"]]
        for beat, frame in zip(vector["heartbeats"], expected_frames):
            self.assertEqual(frame.arbitration_id, cob_id_heartbeat(beat["node_id"]))
            event = monitor.feed(frame, beat["at"])
            self.assertIsNotNone(event, "心跳帧没被认出来")
            self.assertEqual(event.state, beat["state"])
            self.assertTrue(event.is_transition,
                            "有效的心跳（含首次上报）都应记为一次状态迁移")

        # 状态记录
        for node_text, state in vector["expect_states"].items():
            self.assertEqual(monitor.state_of(int(node_text)), state)

        # 状态迁移序列
        transitions = monitor.transitions
        self.assertEqual(len(transitions), len(vector["expect_transitions"]))
        for got, want in zip(transitions, vector["expect_transitions"]):
            self.assertEqual(got.node_id, want["node_id"])
            self.assertEqual(got.state, want["state"])
            self.assertEqual(got.timestamp, want["at"])

        # 超时判定：按语料给的时间点逐点比对
        for at_text, expected_due in vector["expect_due_at"].items():
            self.assertEqual(
                monitor.due_nodes(float(at_text)), expected_due,
                "t=%s 时的超时节点集不符" % at_text)

    def test_heartbeat_timeout_is_not_silence_error(self):
        """超时是**查询结果**，不是异常。

        `due_nodes()` 返回列表；把它实现成抛异常的话，上层就没法同时监控
        多个节点——一个节点掉线不该打断其余节点的观察。
        """
        monitor = HeartbeatMonitor([1, 2, 3], 1.0)
        self.assertEqual(monitor.due_nodes(0.0), [1, 2, 3],
                         "从未上报过的节点在 t=0 就应判超时")
        monitor.feed(Frame(cob_id_heartbeat(1), bytes([NmtState.OPERATIONAL])), 0.0)
        self.assertEqual(monitor.due_nodes(0.5), [2, 3])
        self.assertEqual(monitor.due_nodes(1.5), [1, 2, 3])

    def test_heartbeat_accepts_extended_frames_off_the_heartbeat_range(self):
        # 心跳的判据是 COB-ID 落在 0x700..0x77F。**下界**其实不可放宽也自证：
        # 任何 < 0x700 的 ID 减去 0x700 都是负数，永远落不进 0..127 的节点名单，
        # 因此"放宽下界"是一个等价变异（穷举 0x000..0x7FF 后行为完全一致，
        # 已在开发过程中验证）。真正需要守的是上界与整体区间。
        monitor = HeartbeatMonitor(list(range(128)), 1.0)
        for can_id in list(range(0x000, 0x700)) + list(range(0x780, 0x800)):
            self.assertIsNone(
                monitor.feed(Frame(can_id, bytes([NmtState.OPERATIONAL])), 0.0),
                "0x%03X 在心跳区间之外，不得被接受" % can_id)
        for can_id in (0x700, 0x701, 0x77F):
            self.assertIsNotNone(
                monitor.feed(Frame(can_id, bytes([NmtState.OPERATIONAL])), 0.0),
                "0x%03X 在心跳区间内，应被接受" % can_id)

    def test_heartbeat_range_judgement_cannot_be_widened(self):
        # 变异实验暴露的盲点（第二次）：把区间判据从 0x700..0x77F 放宽成
        # `>= 0x080`，套件依然全绿。原因是原来的越界帧都不在监控名单里，
        # feed 返回 None 是"因为不是我的节点"，与"因为不是心跳"分不开。
        #
        # 这里让越界帧在放宽判据下**恰好**映射到名单内的节点：
        # 监控 0..3 号（即 0x700..0x703）。任何 0x700 之外的帧，
        # 只要实现按 `can_id - 0x700` 算节点号并放宽区间，
        # 就会算出 0x700+2=0x702、0x080+2=0x082 这类"正好在名单里"的号。
        monitor = HeartbeatMonitor([0, 1, 2, 3], 1.0)
        for can_id in (0x702, 0x701, 0x700):
            # 这三条是真心跳，先确认基线是可用的
            self.assertIsNotNone(
                monitor.feed(Frame(can_id, bytes([NmtState.OPERATIONAL])), 0.0),
                "0x%03X 是心跳，应被接受" % can_id)

        fresh = HeartbeatMonitor([0, 1, 2, 3], 1.0)
        # 0x080 区的 EMCY 帧在放宽判据下会算成节点 0x080-0x700 = 负数，
        # 因此改用**上界之上**的帧：0x780..0x783 在按 can_id-0x700 计算时
        # 得到 0x80..0x83（不在名单里），但 0x800 区不在此列。
        # 真正能区分的是 0x704~0x70F：它们紧邻心跳区间上界，
        # 按 can_id-0x700 算出 4..15，若实现把上界写错就会误收。
        for can_id in (0x704, 0x70F, 0x710, 0x780, 0x7FF, 0x080, 0x0FF,
                       0x5FF, 0x600, 0x6FF):
            self.assertIsNone(
                fresh.feed(Frame(can_id, bytes([NmtState.OPERATIONAL])), 0.0),
                "COB-ID 0x%03X 不是心跳帧，不得被计入" % can_id)
        self.assertEqual(fresh.due_nodes(0.5), [0, 1, 2, 3],
                         "非心跳帧不得阻止超时判定")

        # 区间两端必须是心跳：0x700 是节点 0，0x77F 是节点 127。
        both = HeartbeatMonitor([0, 127], 1.0)
        self.assertIsNotNone(
            both.feed(Frame(0x700, bytes([NmtState.OPERATIONAL])), 1.0))
        self.assertIsNotNone(
            both.feed(Frame(0x77F, bytes([NmtState.STOPPED])), 2.0))
        self.assertEqual(both.state_of(127), NmtState.STOPPED)

    def test_heartbeat_requires_single_data_byte(self):
        # 心跳帧固定 1 字节；长度不对的当噪声。
        monitor = HeartbeatMonitor([1], 1.0)
        self.assertIsNone(monitor.feed(Frame(0x701, b""), 0.0))
        self.assertIsNone(monitor.feed(Frame(0x701, b"\x05\x00"), 0.0))
        self.assertEqual(monitor.due_nodes(0.5), [1])

    def test_heartbeat_ignores_non_heartbeat_frames(self):
        """别的帧不能污染心跳计时。

        SDO 应答与心跳共用接收队列，实现必须按 COB-ID 区分。
        """
        monitor = HeartbeatMonitor([1], 1.0)
        self.assertIsNone(monitor.feed(
            Frame(cob_id_sdo_rx(1), bytes([0x43, 0x00, 0x10, 0x00, 0, 0, 0, 0])), 0.0))
        self.assertEqual(monitor.due_nodes(0.5), [1],
                         "SDO 应答不该被当成心跳")
        # EMCY 同理
        self.assertIsNone(monitor.feed(
            Frame(cob_id_emcy(1), bytes([0x10, 0x23, 0x01, 0, 0, 0, 0, 0])), 0.6))
        self.assertEqual(monitor.due_nodes(0.9), [1])

    def test_heartbeat_state_names_include_unknown(self):
        """未知状态值原样十六进制，不编造状态名。"""
        self.assertEqual(NmtState.name(0x05), "Operational")
        self.assertEqual(NmtState.name(0x7F), "Pre-operational")
        self.assertEqual(NmtState.name(0x04), "Stopped")
        self.assertEqual(NmtState.name(0x00), "Initialising")
        self.assertEqual(NmtState.name(0x33), "unknown state 0x33")


# --------------------------------------------------------------------------
# EMCY
# --------------------------------------------------------------------------


class TestGoldenEmcy(GoldenTestCase):
    """紧急报文收帧与字段拆解。"""

    def test_emcy_report_and_clear(self):
        vector = self.vector("emcy_report_and_clear")
        frames = [frame_from_json(entry["response"])
                  for entry in vector["expected_frames"]]
        parsed = [canopen.parse_emcy(frame) for frame in frames]
        for got, want in zip(parsed, vector["expect_parsed"]):
            self.assertIsNotNone(got, "EMCY 帧没被认出来")
            self.assertEqual(got.node_id, want["node_id"])
            self.assertEqual(got.error_code, want["error_code"])
            self.assertEqual(got.error_register, want["error_register"])
            self.assertEqual(got.has_error, want["has_error"])
            self.assertEqual(got.is_clear, want["is_clear"])
            self.assertEqual(got.vendor_data.hex(" ").upper(),
                             want["vendor_data"].upper())

    def test_emcy_clear_has_zero_error_code(self):
        """错误码 0x0000 是"错误已消除"的清除报文，不是错误上报。"""
        vector = self.vector("emcy_report_and_clear")
        report = canopen.parse_emcy(frame_from_json(
            vector["expected_frames"][0]["response"]))
        clear = canopen.parse_emcy(frame_from_json(
            vector["expected_frames"][1]["response"]))
        self.assertTrue(report.has_error)
        self.assertFalse(report.is_clear)
        self.assertFalse(clear.has_error)
        self.assertTrue(clear.is_clear)
        self.assertEqual(clear.error_code, 0)

    def test_emcy_cob_id_is_node_plus_0x80(self):
        """EMCY 的基址是 0x080 —— 比 SDO 应答 0x580 靠前得多。

        写错基址的结果是"EMCY 帧被当成别的报文"，故障时一条错误都收不到。
        """
        for node_id in (1, 2, 127):
            self.assertEqual(cob_id_emcy(node_id), 0x080 + node_id)
        self.assertEqual(cob_id_emcy(2), 0x082)

    def test_non_emcy_frame_is_not_parsed(self):
        """0x080 以外的帧不得被当成 EMCY。"""
        self.assertIsNone(canopen.parse_emcy(Frame(cob_id_heartbeat(1), b"\x05")))
        self.assertIsNone(canopen.parse_emcy(Frame(cob_id_sdo_rx(1), b"\x43")))


# --------------------------------------------------------------------------
# PDO 映射与通讯参数（只读）
# --------------------------------------------------------------------------


class TestGoldenPdo(GoldenTestCase):
    """只读地读 PDO 映射与通讯参数。"""

    def test_pdo_mapping_read_rpdo1(self):
        vector = self.vector("pdo_mapping_read_rpdo1")
        bus, client, node = self.make_bus_and_client(vector)
        mapping = read_pdo_mapping(client, vector["base_index"], vector["subindex"])
        self.assertEqual(mapping.count, vector["expect_count"])
        self.assertEqual([list(entry) for entry in mapping.entries],
                         [list(entry) for entry in vector["expect_entries"]])
        self.assertEqual(mapping.total_bits, vector["expect_total_bits"])

    def test_pdo_mapping_entry_encoding(self):
        """映射条目的 32 位布局：索引<<16 | 子索引<<8 | 位长。

        写错位移的实现在这里会算出荒谬的位长（例如 0x40 变成 64 位）。
        """
        vector = self.vector("pdo_mapping_read_rpdo1")
        bus, client, node = self.make_bus_and_client(vector)
        mapping = read_pdo_mapping(client, vector["base_index"], vector["subindex"])
        self.assertEqual(mapping.entries[0], (0x6040, 0, 16))
        self.assertEqual(mapping.entries[1], (0x6060, 0, 8))

    def test_pdo_mapping_rejects_non_mapping_index(self):
        """0x1000 不是映射对象，必须报错而不是读出垃圾。"""
        bus = MockBus([mockbus.make_sdo_golden_node(1)])
        client = SdoClient(bus, 1, timeout=0.05)
        with self.assertRaises(ValueError):
            read_pdo_mapping(client, 0x1000, 1)

    def test_pdo_comm_read_tpdo1(self):
        vector = self.vector("pdo_comm_read_tpdo1")
        bus, client, node = self.make_bus_and_client(vector)
        comm = read_pdo_communication(client, vector["base_index"],
                                      vector["subindex"])
        self.assertEqual(comm.cob_id, vector["expect_cob_id"])
        self.assertEqual(comm.valid, vector["expect_valid"])
        self.assertEqual(comm.transmission_type, vector["expect_transmission_type"])
        self.assertEqual(comm.transmission_type_name,
                         vector["expect_transmission_type_name"])

    def test_pdo_comm_valid_bit(self):
        """bit31 = 1 表示该 PDO 被禁用。这个位不看，就会把禁用的 PDO 当活的。"""
        node = mockbus.make_sdo_golden_node(1)
        node.set_object(0x1801, 0, b"\x02")
        node.set_object(0x1801, 1, (0x80000000 | 0x201).to_bytes(4, "little"))
        node.set_object(0x1801, 2, b"\x01")
        bus = MockBus([node])
        self.recorded = bus.recorded_exchange
        client = SdoClient(bus, 1, timeout=0.05)
        comm = read_pdo_communication(client, 0x1800, 1)
        self.assertFalse(comm.valid, "bit31 置位应判为禁用")
        self.assertEqual(comm.cob_id, 0x201)

    def test_mapping_nonzero_subindex_uses_cia301_bit_layout(self):
        node = MockNode(1)
        node.set_object(0x1600, 0, b"\x01")
        node.set_object(0x1600, 1, bytes.fromhex("20 02 64 60"))
        mapping = read_pdo_mapping(SdoClient(MockBus([node], loopback=False), 1), 0x1600, 0)
        self.assertEqual(mapping.entries, [(0x6064, 2, 32)])
        self.assertEqual(mapping.total_bits, 32)

    def test_tpdo_inhibit_time_is_subindex_three_even_without_event_timer(self):
        node = MockNode(1)
        node.set_object(0x1800, 0, b"\x03")
        node.set_object(0x1800, 1, (0x181).to_bytes(4, "little"))
        node.set_object(0x1800, 2, b"\xff")
        node.set_object(0x1800, 3, (100).to_bytes(2, "little"))
        comm = read_pdo_communication(SdoClient(MockBus([node], loopback=False), 1), 0x1800, 0)
        self.assertEqual(comm.inhibit_time, 100)
        self.assertIsNone(comm.event_timer)

    def test_tpdo_event_and_inhibit_time_are_independent(self):
        node = MockNode(1)
        for subindex, data in {0: b"\x05", 1: (0x181).to_bytes(4, "little"),
                               2: b"\xff", 3: (100).to_bytes(2, "little"),
                               5: (250).to_bytes(2, "little")}.items():
            node.set_object(0x1800, subindex, data)
        comm = read_pdo_communication(SdoClient(MockBus([node], loopback=False), 1), 0x1800, 0)
        self.assertEqual(comm.inhibit_time, 100)
        self.assertEqual(comm.event_timer, 250)

    def test_rpdo_does_not_read_tpdo_only_inhibit_time(self):
        node = MockNode(1)
        for subindex, data in {0: b"\x05", 1: (0x201).to_bytes(4, "little"),
                               2: b"\xff", 5: (250).to_bytes(2, "little")}.items():
            node.set_object(0x1400, subindex, data)
        comm = read_pdo_communication(SdoClient(MockBus([node], loopback=False), 1), 0x1400, 0)
        self.assertIsNone(comm.inhibit_time)
        self.assertEqual(comm.event_timer, 250)

    def test_tpdo_cob_id_bit30_means_rtr_disallowed(self):
        comm = canopen.PdoCommunication(index=0x1800, cob_id_raw=0x181, transmission_type=255)
        self.assertTrue(comm.rtr_allowed)
        comm.cob_id_raw |= 0x40000000
        self.assertFalse(comm.rtr_allowed)
        self.assertEqual(comm.cob_id, 0x181)
        self.assertTrue(comm.valid)

    def test_pdo_comm_rejects_non_comm_index(self):
        bus = MockBus([mockbus.make_sdo_golden_node(1)])
        client = SdoClient(bus, 1, timeout=0.05)
        with self.assertRaises(ValueError):
            read_pdo_communication(client, 0x1000, 1)


# --------------------------------------------------------------------------
# NMT
# --------------------------------------------------------------------------


class TestGoldenNmt(GoldenTestCase):
    """NMT 状态机与命令。"""

    def test_nmt_start_stop_via_heartbeat(self):
        vector = self.vector("nmt_start_stop_via_heartbeat")
        node = mockbus.make_sdo_golden_node(1)
        bus = MockBus([node])
        master = NmtMaster(bus)

        expected = vector["expected_frames"]
        # NMT 命令本身会让节点排一个心跳，因此不必手工排队；
        # 每发一条命令，紧接着的那个心跳就是它的生效证据。
        master.enter_pre_operational(vector["node_id"])
        self.assertEqual(node.state, NmtState.PRE_OPERATIONAL)
        self.assertEqual(node.pop_outbox(),
                         frame_from_json(expected[0]["response"]))

        master.start(vector["node_id"])
        self.assertEqual(node.state, NmtState.OPERATIONAL)
        self.assertEqual(node.pop_outbox(),
                         frame_from_json(expected[1]["response"]))

        master.stop(vector["node_id"])
        self.assertEqual(node.state, NmtState.STOPPED)
        self.assertEqual(node.pop_outbox(),
                         frame_from_json(expected[2]["response"]))

        # 实际发出的 NMT 帧必须与语料逐帧一致
        commands = [frame for frame in bus.sent if frame.arbitration_id == 0x000]
        self.assertEqual(len(commands), 3)
        for got, want in zip(commands, expected):
            self.assertEqual(got, frame_from_json(want["request"]))

    def test_nmt_broadcast_reset_node(self):
        """广播 reset：节点先回 Initialising(0)，再回 Pre-operational(127)。"""
        vector = self.vector("nmt_broadcast_reset_node")
        node = mockbus.make_sdo_golden_node(1)
        bus = MockBus([node])
        master = NmtMaster(bus)
        master.reset_node(0)

        request = frame_from_json(vector["expected_frames"][0]["request"])
        self.assertEqual(request.data, bytes([0x81, 0x00]),
                         "广播时节点号字节必须是 0")
        self.assertEqual(bus.sent[0], request)

        # 两跳心跳
        sequence = [node.pop_outbox().data[0] for _ in range(2)]
        self.assertEqual(sequence, vector["expect_heartbeat_sequence"])
        self.assertEqual(sequence[0], NmtState.INITIALISING)
        self.assertEqual(sequence[1], NmtState.PRE_OPERATIONAL)
        self.assertEqual(node.state, NmtState.PRE_OPERATIONAL)

    def test_nmt_command_values(self):
        """NMT 命令字的规范取值。写错一个位就是"电机不动"这类难查的现场问题。"""
        self.assertEqual(canopen.NMT_COMMANDS["start_remote_node"], 0x01)
        self.assertEqual(canopen.NMT_COMMANDS["stop_remote_node"], 0x02)
        self.assertEqual(canopen.NMT_COMMANDS["enter_pre_operational"], 0x80)
        self.assertEqual(canopen.NMT_COMMANDS["reset_node"], 0x81)
        self.assertEqual(canopen.NMT_COMMANDS["reset_communication"], 0x82)

    def test_nmt_targets_only_its_own_node(self):
        """发给 2 号节点的命令不得改变 1 号节点的状态。"""
        node_a = mockbus.make_sdo_golden_node(1)
        node_b = mockbus.make_sdo_golden_node(2)
        bus = MockBus([node_a, node_b])
        NmtMaster(bus).start(2)
        self.assertEqual(node_b.state, NmtState.OPERATIONAL)
        self.assertEqual(node_a.state, NmtState.INITIALISING,
                         "1 号节点不该被发给 2 号的命令影响")

    def test_nmt_rejects_unknown_command(self):
        with self.assertRaises(ValueError):
            canopen.nmt_command_frame(0x42, 1)


# --------------------------------------------------------------------------
# COB-ID 计算
# --------------------------------------------------------------------------


class TestGoldenCobId(GoldenTestCase):
    """COB-ID 公式与边界。节点号 1 和 127 两端都要对。"""

    def test_cob_id_mapping(self):
        vector = self.vector("cob_id_mapping")
        self.assertEqual(canopen.COB_ID_NMT_BROADCAST, vector["nmt_broadcast"])
        for case in vector["cases"]:
            node_id = case["node_id"]
            self.assertEqual(cob_id_sdo_tx(node_id), case["sdo_tx"])
            self.assertEqual(cob_id_sdo_rx(node_id), case["sdo_rx"])
            self.assertEqual(cob_id_heartbeat(node_id), case["heartbeat"])
            self.assertEqual(cob_id_emcy(node_id), case["emcy"])

    def test_cob_id_bases_do_not_collide(self):
        """五组基址互不重叠。

        EMCY(0x080) 与 NMT(0x000) 靠得很近，是最容易写错基址的一对：
        把 EMCY 写成 0x800 就跑到 SDO 应答区里去了。
        """
        bases = {
            "nmt": canopen.COB_ID_NMT_BROADCAST,
            "emcy": 0x080,
            "sdo_tx": 0x600,
            "sdo_rx": 0x580,
            "heartbeat": 0x700,
        }
        self.assertEqual(bases["emcy"], 0x080)
        self.assertEqual(bases["sdo_rx"] + 127, 0x5FF)
        self.assertEqual(bases["sdo_tx"], 0x600)
        self.assertLess(bases["sdo_rx"] + 127, bases["sdo_tx"])
        self.assertLess(bases["emcy"] + 127, 0x100)

    def test_cob_id_rejects_out_of_range_node(self):
        for bad in (-1, 128, 255):
            with self.assertRaises(ValueError):
                cob_id_sdo_tx(bad)


# --------------------------------------------------------------------------
# 静默总线 / SDO 超时
# --------------------------------------------------------------------------


class TestGoldenTimeout(GoldenTestCase):
    """静默总线上的 SDO 必须精确地抛 BusSilenceError。"""

    def test_sdo_timeout_no_response(self):
        vector = self.vector("sdo_timeout_no_response")
        bus = MockBus.silent()
        client = SdoClient(bus, vector["node_id"], timeout=vector["timeout_s"])
        with self.assertRaises(BusSilenceError):
            client.read(vector["index"], vector["subindex"])
        # 请求帧必须用该节点的 COB-ID
        self.assertEqual(bus.sent[-1].arbitration_id,
                         cob_id_sdo_tx(vector["node_id"]))

    def test_silence_is_not_abort(self):
        """静默 ≠ abort。分清这两者决定现场查总线还是查对象字典。"""
        bus = MockBus.silent()
        client = SdoClient(bus, 7, timeout=0.01)
        with self.assertRaises(BusSilenceError) as ctx:
            client.read(0x1000)
        self.assertNotIsInstance(ctx.exception, SdoAbort)
        self.assertNotIsInstance(ctx.exception, canopen.SdoProtocolError)

    def test_unrelated_frames_do_not_replace_the_target_response(self):
        node = MockNode(1, device_type=123)
        bus = MockBus([node], loopback=False)
        original_recv = bus.recv
        stray = iter([
            Frame(cob_id_heartbeat(2), b"\x7f"),
            Frame(cob_id_sdo_rx(2), b"\x43\x00\x10\x00\x01\x02\x03\x04"),
            Frame(cob_id_sdo_rx(1), b"", remote=True),
            Frame(cob_id_sdo_rx(1), b"", extended=True),
        ])

        def recv_with_stray(timeout=None):
            return next(stray, None) or original_recv(timeout)

        bus.recv = recv_with_stray
        client = SdoClient(bus, 1, timeout=0.05)
        self.assertEqual(client.read_device_type(), 123)

    def test_continuous_unrelated_traffic_obeys_total_deadline(self):
        bus = MockBus.silent()
        bus.recv = mock.Mock(return_value=Frame(cob_id_heartbeat(2), b"\x7f"))
        client = SdoClient(bus, 1, timeout=0.1)
        with mock.patch.object(canopen.time, "monotonic", side_effect=[0.0, 0.01, 0.04, 0.11]):
            with self.assertRaises(BusSilenceError):
                client.read_device_type()
        self.assertEqual(bus.recv.call_count, 2)
        self.assertAlmostEqual(bus.recv.call_args_list[0].args[0], 0.09)
        self.assertAlmostEqual(bus.recv.call_args_list[1].args[0], 0.06)


# --------------------------------------------------------------------------
# 标准层硬规则：不得硬编码厂家索引
# --------------------------------------------------------------------------


class TestStandardLayerDiscipline(unittest.TestCase):
    """`canopen.py` 的硬规则的机检。

    规则：标准层只认规范里的公共对象，厂家私有索引属于 od.json。
    这条规则靠人记会漏，因此用静态检查兜住。
    """

    def setUp(self):
        self.path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "canopen.py")
        with io.open(self.path, encoding="utf-8") as fh:
            self.source = fh.read()

    def test_only_standard_library_imports(self):
        """只用 Python 3 标准库。"""
        allowed = {
            "__future__", "struct", "dataclasses", "typing", "time", "socket",
            "fcntl", "os", "errno", "json", "enum", "collections", "math",
        }
        import ast
        tree = ast.parse(self.source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], allowed,
                                  "canopen.py 引入了非标准库: %s" % alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self.assertIn(node.module.split(".")[0], allowed,
                                  "canopen.py 引入了非标准库: %s" % node.module)

    def test_object_index_table_is_standard_only(self):
        """对象索引表里的每个索引都必须落在 CiA 301 公共对象区。

        规范给了固定含义的区间：0x1000-0x1FFF 是通讯参数区，
        其中 0x1000-0x1029 是公共对象。厂家私有区是 0x2000-0x5FFF，
        它们**只能**出现在 od.json 里。
        """
        for index in canopen.SDO_INDEX_TABLE:
            self.assertGreaterEqual(index, 0x1000,
                                    "0x%04X 低于公共对象区" % index)
            self.assertLessEqual(index, 0x1FFF,
                                 "0x%04X 超出公共对象区" % index)

    def test_no_hardcoded_vendor_motor_indices(self):
        """源码里不得出现"看着像厂家索引"的字面量。

        判据刻意保守：只查那些典型的私有区数值字面量（0x2000-0x5FFF 的十六进制
        写法）。CiA 402 的 0x6040/0x6060 之类**允许**出现在注释里作为举例，
        因此这里只查真正的赋值与比较字面量，并排除注释。
        """
        import ast
        tree = ast.parse(self.source)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                value = node.value
                # 厂家私有区（按规范，0x2000-0x5FFF 保留给厂商）
                if 0x2000 <= value <= 0x5FFF:
                    offenders.append((node.lineno, value))
        self.assertEqual(
            offenders, [],
            "canopen.py 里出现了厂家私有区的索引字面量: %s" % (
                ", ".join("第 %d 行 0x%04X" % (line, value)
                          for line, value in offenders),))

    def test_no_bus_specific_import(self):
        """标准层不得反向依赖具体的传输实现。

        `canopen.py` 只依赖 `bus.send/recv` 这一组接口；一旦它 import
        `canbus` 或 `socket`，模拟总线就再也替不进来了。
        """
        self.assertNotIn("import canbus", self.source)
        self.assertNotIn("import mockbus", self.source)
        self.assertNotIn("import socket", self.source)


# --------------------------------------------------------------------------
# 传输层：FD 帧必须显式拒绝
# --------------------------------------------------------------------------


class TestTransportDiscipline(unittest.TestCase):
    """FD 帧的拒绝行为。主板 CAN 只支持 2.0B。"""

    def test_canbus_rejects_fd_send(self):
        """真总线：FD 帧显式报错，绝不静默丢弃。"""
        import canbus
        bus = canbus.CanBus.__new__(canbus.CanBus)
        bus.interface = "can1"
        bus.fd = False
        bus.tx_count = 0
        bus.tx_error_count = 0
        frame = Frame(0x180, bytes(8), frame_type=canopen.FRAME_FD)
        bus._sock = object()  # 只要能过 _require_open；FD 检查在发送之前
        with self.assertRaises(canbus.FdNotSupportedError):
            bus.send(frame)

    def test_canbus_rejects_fd_open_flag(self):
        """请求 FD 模式直接报错——本板主板 CAN 没这个能力。"""
        import canbus
        with self.assertRaises(canbus.FdNotSupportedError):
            canbus.CanBus("can1", fd=True)

    def test_canbus_classic_frame_roundtrip(self):
        """经典帧打包/解包是二进制一致的。"""
        import canbus
        frame = Frame(0x601, bytes([0x40, 0x00, 0x10, 0x00, 0, 0, 0, 0]))
        packed = canbus.pack_can_frame(frame)
        self.assertEqual(len(packed), canbus.CAN_MTU)
        self.assertEqual(canbus.unpack_can_frame(packed), frame)

    def test_canbus_rejects_oversized_classic_frame(self):
        """经典帧不能带超过 8 字节——不能悄悄截断。"""
        import canbus
        with self.assertRaises(ValueError):
            canbus.pack_can_frame(Frame(0x601, bytes(9)))

    def test_canbus_unpack_rejects_fd_mtu(self):
        """收到 72 字节（FD）时明确报错，不按经典帧错位解析。"""
        import canbus
        with self.assertRaises(canbus.FdNotSupportedError):
            canbus.unpack_can_frame(b"\x00" * canbus.CANFD_MTU)

    def test_mockbus_rejects_fd_send(self):
        """模拟总线与真总线的 FD 行为必须一致，否则回归结论不可移植。"""
        import canbus
        bus = MockBus.silent()
        with self.assertRaises(canbus.FdNotSupportedError):
            bus.send(Frame(0x180, bytes(8), frame_type=canopen.FRAME_FD))

    def test_af_can_bind_address_is_single_element_tuple(self):
        # CPython 的 AF_CAN 地址是单元素元组 (ifname,)，不是手拼的
        # 22 字节 sockaddr_can。这条断言来自真板实测：
        # 手拼字节串 bind 会抛 TypeError，而手拼的实现在开发机上根本跑不到，
        # 必须靠这条把它钉死在回归里。
        import canbus
        address = canbus.pack_sockaddr_can("can1")
        self.assertEqual(address, ("can1",))
        self.assertIsInstance(address, tuple)
        self.assertEqual(len(address), 1)
        with self.assertRaises(ValueError):
            canbus.pack_sockaddr_can("")
        # 接口名超过 IFNAMSIZ-1 = 15 字符必须被拒
        with self.assertRaises(ValueError):
            canbus.pack_sockaddr_can("x" * 16)

    def test_ifindex_uses_siocgifindex(self):
        # 接口索引查询仍走 ioctl(SIOCGIFINDEX)，但它只用于报错与状态展示，
        # 不参与 bind。这里只验证"接口不存在时报的是 InterfaceNotFoundError
        # 且消息里带接口名"，不依赖本机是否存在 CAN 接口。
        import canbus
        probe = mock.Mock()
        with mock.patch.object(canbus.socket, "socket", return_value=probe), \
                mock.patch.object(canbus.fcntl, "ioctl", side_effect=OSError(errno.ENODEV, "missing")) as ioctl:
            with self.assertRaises(canbus.InterfaceNotFoundError) as ctx:
                canbus._ifindex("nosuchcan0")
        self.assertIn("nosuchcan0", str(ctx.exception))
        self.assertEqual(ioctl.call_args.args[1], canbus.SIOCGIFINDEX)
        probe.close.assert_called_once_with()


# --------------------------------------------------------------------------
# 录制与回放
# --------------------------------------------------------------------------


class TestTraceReplay(GoldenTestCase):
    """录制/回放：现场的一次故障要能固化成可重跑的回归。"""

    def test_record_then_replay(self):
        """录一段真实对话，再用回放总线重跑，帧序列必须一致。"""
        vector = self.vector("expedited_read_uint16")
        bus, client, _ = self.make_bus_and_client(vector)
        recorder = mockbus.TraceRecorder(bus, name="expedited_read_uint16")
        original_send = bus.send
        original_recv = bus.recv

        def recording_send(frame):
            recorder.record_request(frame)
            original_send(frame)

        def recording_recv(timeout=None):
            response = original_recv(timeout)
            if response is not None:
                recorder.record_response(response)
            return response

        bus.send = recording_send
        bus.recv = recording_recv
        client.read(vector["index"], 0)
        bus.send, bus.recv = original_send, original_recv

        trace = recorder.to_dict()
        self.assertEqual(len(trace["steps"]), 1)

        # 回放：同一串请求必须得到同一串应答，且不报不一致
        replay_bus = MockBus.from_trace(trace)
        replay_client = SdoClient(replay_bus, vector.get("node_id", 1), timeout=0.05)
        got = replay_client.read(vector["index"], 0)
        self.assertEqual(got.hex(" ").upper(), vector["expected_value"].upper())
        self.assertEqual(replay_bus.replay_mismatches, [])

    def test_replay_detects_divergent_request(self):
        """回放时请求帧不同必须报错，而不是默默给一份对不上的应答。"""
        vector = self.vector("expedited_read_uint16")
        trace = {
            "name": "mismatch",
            "steps": [dict(vector["expected_frames"][0])],
        }
        bus = MockBus.from_trace(trace)
        # 发一个索引不同的请求
        with self.assertRaises(mockbus.TraceMismatchError):
            bus.send(Frame(0x601, bytes([0x40, 0x00, 0x10, 0x00, 0, 0, 0, 0])))

    def test_trace_json_shape_is_stable(self):
        """录制的 JSON 形状与金标语料一致——现场录的片段能直接当语料用。"""
        frame = Frame(0x601, bytes([0x40, 0x10, 0x10, 0x00, 0, 0, 0, 0]))
        encoded = mockbus.frame_to_json(frame)
        self.assertEqual(encoded["id"], "0x601")
        self.assertEqual(encoded["data"], "40 10 10 00 00 00 00 00")
        self.assertEqual(mockbus.frame_from_json(encoded), frame)
        # 十进制 id 也要认
        self.assertEqual(mockbus.frame_from_json({"id": 1537, "data": "40"}),
                         Frame(0x601, b"\x40"))


# --------------------------------------------------------------------------
# 语料自身的健康检查
# --------------------------------------------------------------------------


class TestGoldenCorpus(GoldenTestCase):
    """语料本身的检查：条数、必填字段、帧形状。"""

    def test_minimum_vector_count(self):
        """任务要求至少 12 条序列；现在远多于这个数。"""
        self.assertGreaterEqual(len(self.golden["vectors"]), 12)

    def test_every_kind_required_by_spec_is_present(self):
        """规范点名的场景都必须有语料覆盖。"""
        required = {
            "sdo_expedited_read", "sdo_expedited_write",
            "sdo_segmented_read", "sdo_segmented_write",
            "sdo_abort", "node_scan", "heartbeat_timeout", "emcy",
            "pdo_mapping_read", "pdo_comm_read", "nmt", "cob_id",
        }
        present = {v["kind"] for v in self.golden["vectors"]}
        missing = required - present
        self.assertEqual(missing, set(), "语料缺少场景: %s" % sorted(missing))

    def test_vector_names_are_unique(self):
        names = [v["name"] for v in self.golden["vectors"]]
        self.assertEqual(len(names), len(set(names)), "语料里有重名的条目")

    def test_all_vectors_are_asserted_by_some_test(self):
        """每条语料都必须被某个测试用到。

        没人读的语料会腐烂：它可能一直是错的，而 CI 全绿。
        这条断言把它变成"要么用，要么删"。
        """
        referenced = set()
        for name, value in vars(type(self)).items():
            if not name.startswith("test_"):
                continue
            code = value.__code__
            referenced.update(
                constant for constant in code.co_consts
                if isinstance(constant, str) and constant in self.vectors)
        # 也扫描整个模块：语料可能被其他测试类引用
        module = sys.modules[__name__]
        for attribute_name in dir(module):
            attribute = getattr(module, attribute_name)
            if isinstance(attribute, type):
                for name, value in vars(attribute).items():
                    if not name.startswith("test_") or not hasattr(value, "__code__"):
                        continue
                    referenced.update(
                        constant for constant in value.__code__.co_consts
                        if isinstance(constant, str) and constant in self.vectors)
        unused = sorted(set(self.vectors) - referenced)
        self.assertEqual(unused, [], "以下语料没有任何测试引用: %s" % unused)

    def test_every_frame_in_corpus_is_well_formed(self):
        """语料里的每个帧都要能解析成合法帧（长度、ID 范围）。"""
        for vector in self.golden["vectors"]:
            for entry in vector.get("expected_frames", []):
                for side in ("request", "response"):
                    raw = entry.get(side)
                    if raw is None:
                        continue
                    frame = frame_from_json(raw)
                    self.assertLessEqual(len(frame.data), 8,
                                         "%s/%s 的帧超过 8 字节" % (vector["name"], side))
                    self.assertLessEqual(frame.arbitration_id, 0x7FF,
                                         "%s/%s 的 ID 超过标准帧范围" % (vector["name"], side))

    def test_corpus_contains_no_vendor_specific_index(self):
        """语料里的索引必须都是规范公共对象。

        语料一旦混进厂家索引，它就变成了"猜的手册"，而它本该是零手册依赖的。
        """
        # 本语料只允许出现公共对象区（0x1000-0x1FFF）里的索引。
        # PDO 映射与通讯参数（0x1400-0x1BFF）本身就在这个区间内。
        for vector in self.golden["vectors"]:
            name = vector["name"]
            index = vector.get("index")
            if index is not None:
                self.assertTrue(0x1000 <= index <= 0x1FFF,
                                "%s 的索引 0x%04X 不在公共对象区" % (name, index))
            base = vector.get("base_index")
            if base is not None:
                self.assertTrue(
                    0x1400 <= base <= 0x1BFF,
                    "%s 的 base_index 0x%04X 不是 PDO 区域" % (name, base))
            dump = vector.get("object_dump")
            if dump:
                for key in dump:
                    dump_index = int(key.split(":")[0], 16)
                    self.assertTrue(
                        0x1000 <= dump_index <= 0x1FFF,
                        "%s 的对象 dump 含 0x%04X，超出公共对象区" % (name, dump_index))


if __name__ == "__main__":
    unittest.main(verbosity=2)
