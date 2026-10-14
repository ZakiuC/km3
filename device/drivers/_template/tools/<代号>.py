#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_model_ 硬件工具（每硬件一个）。

复制到 device/drivers/<型号>/tools/<型号>.py 后改占位型号。

设计约束（来自实测，不是偏好）：

  * 模板**不依赖 tools/hwtui 存在**。骨架必须在 hwtui 落地之前就能编译、能跑
    ``--help``、`--od-dump` 与 `--scan` 的降级路径，否则模板会腐烂成
    「只有把整个工具链写完才能用」的东西。hwtui 到位后自动接上。
  * 运动类子命令在 OD_VERIFIED 为 False 时**拒绝执行**，并打印
    「N 项对象字典未核对」。只警告不拒绝是不够的：动错方向的电机不会因为
    屏幕上有一行黄字就停下来。
  * 板上 ``stty size`` 返回 ``0 0``（实测），因此 TUI 必须能处理尺寸不可知；
    本文件不自己去问终端尺寸，一律交给 hwtui.tui。
  * 板上没有 vcan，回归走 CAN 控制器回环（见 --test 的实现）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

# --- 路径：本文件在 device/drivers/<型号>/tools/ 下，仓库根要往上四层 ---
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_DRIVER_DIR = os.path.abspath(os.path.join(_HERE, "..", "driver"))

# 逻辑总线名（**不是**内核接口名）。内核接口名一律从 device.json 按它解析。
# 本文件不得出现 can0/can1/spi0 这类物理标识：同一事实两处定义迟早分家，
# 而分家的表现是"工具连的是另一条总线"——现场最难反查的一类故障。
_DEFAULT_BUS = "can-2"

# device.json 的查找顺序：板端置备路径 -> 仓库内模板（未置备的开发机）。
_DEVICE_JSON_CANDIDATES = (
    "/etc/km3/device.json",
    os.path.join(_REPO_ROOT, "device", "config", "device.json"),
)

# 让 `import hwtui.*` 既能从仓库根找到（tools/hwtui/...），
# 也能从 tools/ 找到（hwtui/...）——两种布局在不同部署方式下都出现过。
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# --------------------------------------------------------------------------
# hwtui 的可用性
# --------------------------------------------------------------------------
#
# **刻意不在模块顶层 import hwtui**：那会让本文件在 hwtui 未落地时直接
# ImportError，连 --help 都跑不了，模板随即腐烂。改为按需导入，
# 并在缺失时给出可操作的提示。

_HWTUI_ERROR = None
tui = canbus = canopen = discover = None

try:                                          # pragma: no cover - 环境相关
    from hwtui import tui, canbus, canopen, discover  # type: ignore
except ImportError as _exc:                   # 允许两种包布局
    try:
        import tui, canbus, canopen, discover  # type: ignore
    except ImportError:
        _HWTUI_ERROR = str(_exc)

#: NMT 命令名 -> 命令码。**从 canopen 模块取，不在这里抄一份**：
#: 抄一份就多了一处会漂移的真相。hwtui 未落地时退化为空表，
#: argparse 会因此不许任何取值——这正是想要的（宁可不能用，也不要发错命令）。
def _nmt_commands():
    if _HWTUI_ERROR is None:
        return sorted(canopen.NMT_COMMANDS.keys())  # type: ignore[union-attr]
    return []


def _require_hwtui(feature: str) -> None:
    if _HWTUI_ERROR is None:
        return
    sys.stderr.write(
        "无法导入 tools/hwtui（%s）——%s 需要它。\n"
        "  仓库内应有 tools/hwtui/{tui,canopen,discover}.py。\n"
        "  原始错误：%s\n" % ("/".join([_HWTUI_ERROR]), feature, _HWTUI_ERROR)
    )
    raise SystemExit(2)


# --------------------------------------------------------------------------
# 对象字典状态
# --------------------------------------------------------------------------
#
# 单一事实来源是 driver/od.json。**不硬编码 N，也不解析 od_generated.hpp**：
# 前者会与 od.json 漂移，后者是生成物、格式会变。
# 判据与 C++ 侧的 od::kOdVerified 必须同源，否则工具说"可以动"、
# 驱动说"编不过"，两边都以为自己是对的。


def load_od():
    path = os.path.join(_DRIVER_DIR, "od.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        sys.stderr.write("无法读取 %s：%s\n" % (path, exc))
        raise SystemExit(2)


def od_status():
    """返回 (全部核对?, 未核对条数, 未核对条目名列表)。"""
    od = load_od()
    bad = [e for e in od.get("entries", []) if e.get("status") != "verified"]
    return (not bad), len(bad), [e.get("name", "?") for e in bad]


def refuse_motion(action: str):
    """运动类子命令的统一闸门。"""
    verified, count, names = od_status()
    if verified:
        return
    # 措辞固定：`--init` 与运维脚本会 grep 这一行。
    sys.stderr.write("%d 项对象字典未核对，拒绝执行「%s」。\n" % (count, action))
    sys.stderr.write("未核对的条目：%s\n" % ", ".join(names))
    sys.stderr.write(
        "全部核对（status 改为 verified 并补 cite）后重新生成 od_generated.hpp，"
        "再运行本命令。见 driver/od.json 顶部注释。\n"
    )
    raise SystemExit(3)


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------

# 总线打开的统一入口。
#
# loopback 与 receive_own_messages 都开：调试与无电机回归依赖它们，
# 而**生产实时路径应关**（自己的请求会混进接收队列）——本工具是调试工具，
# 不承担生产职责，因此默认开。
#
# 比特率**不在这里设**。`ip link set` 在板端对 km3 用户是
# "Operation not permitted"（实测），改比特率需要 root 或 polkit 规则，
# 那是置备脚本的职责（/etc/km3/can.env 由 km3-can.service 消费）。
# 这里只读地报告当前值，改不动就明说，不做"看起来改了"的假动作。


def _open_bus(args):
    _require_hwtui("总线操作")
    try:
        return canbus.open_can_bus(args.interface, loopback=True,
                                   receive_own_messages=True)
    except canbus.CanBusError as exc:
        sys.stderr.write("打开 %s 失败：%s\n" % (args.interface, exc))
        raise SystemExit(2)


def _report_bitrate(interface, expected):
    """报告实际比特率与期望值是否一致。只读，不尝试修改。"""
    actual = None
    try:
        with open("/sys/class/net/%s/type" % interface) as fh:
            fh.read()
    except OSError:
        return
    # 比特率不在 sysfs；`ip -details` 才给。没有 ip 命令时静默跳过，
    # 不因为读不到一个诊断字段就让整个命令失败。
    try:
        out = subprocess.run(["ip", "-details", "link", "show", interface],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return
    for token in out.split():
        if token.startswith("bitrate"):
            actual = token.split()[-1] if " " in token else token
    if actual is None:
        for i, token in enumerate(out.split()):
            if token.startswith("bitrate") and i + 1 < len(out.split()):
                actual = out.split()[i + 1]
    if actual is None:
        return
    try:
        got = int(actual)
    except ValueError:
        return
    if got != expected:
        sys.stderr.write(
            "注意：%s 当前比特率 %d，与期望 %d 不一致。\n"
            "改比特率需要 root（板端 km3 用户执行 ip link set 会被拒），"
            "属置备脚本职责。\n" % (interface, got, expected))


def cmd_init(args) -> int:
    """显示/写入本型号的接入参数。**不动作、不使能。**"""
    od = load_od()
    verified, count, names = od_status()

    cfg = {
        "model": od.get("model"),
        "protocol": od.get("protocol"),
        "logical_bus": args.bus,
        "node_id": args.node_id,
        "od_verified": verified,
        "od_unverified_count": count,
    }

    if args.json:
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
    else:
        print("型号      : %s" % cfg["model"])
        print("协议      : %s" % cfg["protocol"])
        print("逻辑总线  : %s   （内核接口名只出现在 device/config/device.json）"
              % cfg["logical_bus"])
        print("节点号    : %s" % cfg["node_id"])
        if verified:
            print("对象字典  : 已全部核对")
        else:
            print("对象字典  : %d 项未核对 -> 运动类子命令将被拒绝" % count)
            for name in names:
                print("            - %s" % name)

    if args.write_config:
        # 板端根分区余量很小，日志/配置必须落 /data 或 /etc，
        # 不写进根分区里随便一个目录。默认路径受 args 控制，调用方负责给对。
        with open(args.write_config, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print("已写入 %s" % args.write_config)
    return 0


def cmd_test(args) -> int:
    """自检：OD 状态 + 可选回环收发。

    板上没有 vcan（CONFIG_CAN_VCAN 未开），因此回归手段是 CAN 控制器的
    **硬件回环**（canbus.CanBus 的 loopback + receive_own_messages）。
    它不需要对端节点，能把「软件错」与「接线错/设备不在线」分开。
    **它不验证电气层与真实从站行为**，因此不能替代实机验收。

    回环是允许在未核对状态下运行的**唯一**一类动作测试：
    没有电机、没有对端，动错方向也不会伤人。
    """
    verified, count, names = od_status()
    print("对象字典：%s" % ("已全部核对" if verified else "%d 项未核对" % count))
    for name in names:
        print("  - %s" % name)

    if not args.loopback:
        # 不指定 --loopback 时只做只读检查，绝不碰总线。
        print("未指定 --loopback：只做离线检查，未打开总线。")
        return 0 if verified else 1

    print("回环模式：%s" % ("OD 已核对" if verified else "OD 未核对（回环允许，无电机无对端）"))
    bus = _open_bus(args)
    try:
        _report_bitrate(args.interface, args.bitrate)
        probe = canbus.Frame(arbitration_id=args.can_id,
                             data=bytes([0xDE, 0xAD, 0xBE, 0xEF]))
        bus.send(probe)
        got = bus.recv(timeout=args.timeout)
        if got is None:
            sys.stderr.write(
                "回环未收到自己的帧。检查 %s 是否为 up 且 loopback on：\n"
                "  ip link set %s down\n"
                "  ip link set %s type can bitrate %d loopback on\n"
                "  ip link set %s up\n"
                % (args.interface, args.interface, args.interface,
                   args.bitrate, args.interface))
            return 1
        if got.arbitration_id != args.can_id or bytes(got.data) != bytes(probe.data):
            sys.stderr.write("回环收到的帧与发出的不一致：%s\n" % (got,))
            return 1
        print("回环自检：通过（id=0x%03X，%d 字节）" % (got.arbitration_id, len(got.data)))
        return 0
    finally:
        bus.close()


def cmd_scan(args) -> int:
    """扫描总线上的节点。只读，不改变任何节点状态。

    **用 SDO 读 0x1000 探，不用心跳探**：心跳可能被 0x1017 关掉，
    而一个在 Pre-operational 状态的节点照样回 SDO。
    """
    bus = _open_bus(args)
    try:
        _report_bitrate(args.interface, args.bitrate)
        # 超时是**每个节点**的等待窗口，不是整轮的：scan_nodes 逐个试探，
        # 每试一个节点花一次 timeout。总耗时约 len(nodes) * timeout。
        found = canopen.scan_nodes(bus, timeout=args.timeout,
                                   probe=_probe_device_type if args.deep else None)
    finally:
        bus.close()

    if not found:
        print("未发现节点。\n"
              "注意：总线上没有从站时控制器仍显示 ERROR-ACTIVE —— 那是**正常活动态**，\n"
              "不是故障。真正要看的是错误计数是否在增长（见 --bus-stats）。")
        return 1
    if args.deep:
        bus = _open_bus(args)
        try:
            for node_id in found:
                client = canopen.SdoClient(bus, node_id, timeout=args.timeout)
                try:
                    device_type = client.read_uint32(0x1000, 0)
                    print("  节点 %-3d 设备类型 0x%08X  %s"
                          % (node_id, device_type,
                             canopen.describe_object(0x1000)))
                except canopen.CanopenError as exc:
                    print("  节点 %-3d 读 0x1000 失败：%s" % (node_id, exc))
        finally:
            bus.close()
    else:
        for node_id in found:
            print("  节点 %d" % node_id)
    return 0


def _probe_device_type(client):
    """读规范要求的最小公共对象 0x1000（设备类型）。"""
    client.read(0x1000, 0)


def cmd_nmt(args) -> int:
    """NMT 状态切换。**会改变节点状态，属运动类，必须核对过 OD。**"""
    # 闸门在最前：OD 未核对时必须先看到「N 项对象字典未核对」，
    # 而不是一条"取值非法"的语法错误——后者会把人的注意力引到错误的方向。
    refuse_motion("nmt")
    _require_hwtui("--nmt")

    commands = canopen.NMT_COMMANDS
    if args.command not in commands:
        sys.stderr.write("未知 NMT 命令：%s\n可用：%s\n"
                         % (args.command, ", ".join(sorted(commands))))
        return 2

    bus = _open_bus(args)
    try:
        master = canopen.NmtMaster(bus)
        master.send_command(commands[args.command], args.node_id)
    finally:
        bus.close()
    print("已向节点 %d 发送 NMT 命令 %s" % (args.node_id, args.command))
    return 0


def cmd_od_dump(args) -> int:
    """打印对象字典，标注每条的核对状态与出处。只读，允许未核对状态下运行。"""
    od = load_od()
    entries = od.get("entries", [])
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0
    print("%-30s %-8s %-4s %-8s %-8s %s"
          % ("名称", "索引", "子", "类型", "状态", "出处"))
    for e in entries:
        print("%-30s %-8s %-4s %-8s %-8s %s" % (
            e.get("name", "?"),
            e.get("index", "?"),
            e.get("subindex", "?"),
            e.get("type", "?"),
            e.get("status", "?"),
            e.get("cite") or "—",
        ))
    print("\n值来自 driver/od.json。status=verified 且 cite 非空的条目才算核对过。")
    return 0


def cmd_bus_stats(args) -> int:
    """总线统计（只读）。

    **CAN 专有的错误计数不存在于 sysfs**：re-started / bus-errors / arbit-lost /
    error-warn / error-pass / bus-off 六个计数由 rtnetlink 的 IFLA_INFO_XSTATS
    提供。读 sysfs 的 statistics/ 只会拿到恒为 0 的通用网卡计数，
    这会让人误以为总线健康。

    本工具**不自己解析 netlink**：那是 tools/hwtui 的活儿，且它当前尚未提供
    该函数。因此这里降到 `ip -details -statistics` 的文本输出——它是同一份
    netlink 数据的官方渲染，且板上 can-utils 全套已实测可用。
    等 hwtui 提供结构化读法后改为调用它，不要在本文件里另写一份 netlink 解析。
    """
    if args.json:
        stats = _bus_stats_via_ip(args.interface)
        if stats is None:
            sys.stderr.write("读总线统计失败（需要 ip 命令）。\n")
            return 2
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    print("接口状态：%s" % _interface_state(args.interface))
    print()
    text = _ip_details(args.interface)
    if text is None:
        sys.stderr.write("读总线统计失败（需要 ip 命令）。\n")
        return 2
    print(text.strip())
    print("\n判读：ERROR-ACTIVE 是正常活动态，不是故障。要看的是错误计数是否在增长。")
    return 0


def _ip_details(interface):
    try:
        r = subprocess.run(["ip", "-details", "-statistics", "link", "show", interface],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def _bus_stats_via_ip(interface):
    text = _ip_details(interface)
    if text is None:
        return None
    stats = {"interface": interface}
    for line in text.split("\n"):
        line = line.strip()
        if " " not in line and ":" not in line:
            continue
        parts = line.split()
        for i, tok in enumerate(parts):
            if tok.endswith(":") and i + 1 < len(parts):
                try:
                    stats[tok.rstrip(":")] = int(parts[i + 1])
                except ValueError:
                    pass
    return stats


def _interface_state(interface):
    try:
        with open("/sys/class/net/%s/operstate" % interface) as fh:
            operstate = fh.read().strip()
    except OSError:
        return "unknown（接口不存在？）"
    try:
        with open("/sys/class/net/%s/flags" % interface) as fh:
            flags = int(fh.read().strip(), 16)
        updown = "UP" if flags & 0x1 else "DOWN"
    except (OSError, ValueError):
        updown = "?"
    return "%s (%s)" % (operstate, updown)


def cmd_raw(args) -> int:
    """发一帧原始 CAN 帧。**不解释语义，因此不需要核对 OD**——
    它的用途正是在手册未到、驱动编不过时做电气层与接线验证。
    """
    payload = args.data.replace(" ", "").replace(":", "").replace("-", "")
    if len(payload) % 2 != 0:
        sys.stderr.write("载荷必须为偶数个十六进制字符：%s\n" % args.data)
        return 2
    try:
        data = bytes.fromhex(payload)
    except ValueError:
        sys.stderr.write("载荷不是十六进制：%s\n" % args.data)
        return 2
    if len(data) > 8:
        # 主板 CAN 是 CAN 2.0B，经典帧上限 8 字节；且 dlc 越界不得被静默钳制
        # （HAL 里 IsDlcValid() 与 PayloadLength() 刻意分离，就是这个理由）。
        sys.stderr.write("经典帧上限 8 字节，收到 %d 字节。\n" % len(data))
        return 2
    if not args.extended and args.can_id > 0x7FF:
        sys.stderr.write("标准帧 ID 上限 0x7FF，收到 0x%X。要发扩展帧请加 --extended。\n"
                         % args.can_id)
        return 2

    bus = _open_bus(args)
    try:
        bus.send(canbus.Frame(arbitration_id=args.can_id, data=data,
                              extended=args.extended))
    finally:
        bus.close()
    return 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


# 子命令表：名称 -> (解析器构造, 处理函数)。**只有一个家**，
# 帮助文本与分发都从这里来，新增子命令不会漏改分发。
_SUBCOMMANDS = {}


def _register_parser(sub, name, help_text):
    sp = sub.add_parser(name, help=help_text)
    _SUBCOMMANDS[name] = sp
    return sp


def build_parser() -> argparse.ArgumentParser:
    # 主解析器**不注册这些子命令**，只生成帮助文本。
    #
    # 起因（实测踩到，不是理论）：子命令名以 `--` 开头时，argparse 会把
    # 子解析器后面的选项塞进内部的 `_UNRECOGNIZED_ARGS` 而不是报错，
    # 于是 `tool.py --od-dump` 以 "unrecognized arguments: --od-dump" 退出 2 ——
    # 每个子命令都不可用，而 --help 里它们看起来完全正常。
    # 与其和 argparse 的内部机制较劲，不如自己做一层极薄的分发：
    # argv 的第一个 token 就是子命令名，其余全部交给该子命令的解析器。
    p = argparse.ArgumentParser(
        prog="_model_",
        description="_model_ 硬件工具（TUI 与命令行）。",
        # allow_abbrev 必须关掉：`--test` 与 `--test-x` 这类新增项之间
        # 会突然产生前缀歧义，而这些子命令是给人和运维脚本用的，拼写必须逐字确定。
        allow_abbrev=False,
    )
    sub = p.add_subparsers(dest="command", metavar="<子命令>")

    def add(name, help_text):
        sp = _register_parser(sub, name, help_text)
        sp.add_argument("--interface", default=None,
                        help="内核接口名；省略时按逻辑总线名从 device.json 解析"
                             "（物理标识的唯一来源是那里）")
        sp.add_argument("--bitrate", type=int, default=500000)
        return sp

    sp = add("--init", "显示/写入接入参数（不动作、不使能）")
    sp.add_argument("--bus", default=_DEFAULT_BUS,
                        help="逻辑总线名（不是内核接口名）")
    sp.add_argument("--node-id", type=int, default=1)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--write-config", metavar="PATH",
                    help="把参数写到该路径（板端不要写进根分区）")
    sp.set_defaults(func=cmd_init)

    sp = add("--test", "自检：OD 状态 + 可选回环收发")
    sp.add_argument("--loopback", action="store_true",
                    help="走 CAN 控制器硬件回环（板上无 vcan，这是替代方案）")
    sp.add_argument("--can-id", type=lambda s: int(s, 16), default=0x123)
    sp.add_argument("--timeout", type=float, default=1.0)
    sp.set_defaults(func=cmd_test)

    sp = add("--scan", "扫描总线节点（只读）")
    sp.add_argument("--timeout", type=float, default=0.05,
                    help="每个节点的等待窗口（秒）。扫描总耗时约 节点数 × 本值")
    sp.add_argument("--deep", action="store_true",
                    help="额外读 0x1000 设备类型并打印（默认只判有无应答）")
    sp.set_defaults(func=cmd_scan)

    sp = add("--nmt", "NMT 状态切换（运动类）")
    sp.add_argument("--node-id", type=int, default=1)
    sp.add_argument("--command", required=True, metavar="COMMAND",
                    help="NMT 命令名（取值来自 tools/hwtui/canopen.py 的 NMT_COMMANDS：%s）"
                         % ", ".join(_nmt_commands()) or "（hwtui 未落地）")
    sp.set_defaults(func=cmd_nmt)

    sp = add("--od-dump", "打印对象字典与核对状态（只读）")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_od_dump)

    sp = add("--bus-stats", "总线统计（只读，走 netlink 不读 sysfs）")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_bus_stats)

    sp = add("--raw", "发一帧原始 CAN 帧（不解释语义）")
    sp.add_argument("--can-id", type=lambda s: int(s, 16), required=True)
    sp.add_argument("--data", required=True, help='十六进制，如 "DE AD BE EF"')
    sp.add_argument("--extended", action="store_true", help="29 位扩展帧")
    sp.set_defaults(func=cmd_raw)

    return p


def resolve_interface(logical_bus, override=None):
    """把逻辑总线名解析成内核接口名。

    物理标识（内核接口名）的**唯一来源是 device.json**。命令行可显式覆盖，
    但代码里的默认值绝不能是物理名——否则模板每复制一次，那条事实就多一份副本。
    """
    if override:
        return override
    for path in _DEVICE_JSON_CANDIDATES:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        for item in (cfg.get("bus") or {}).get("can", []):
            if item.get("logical") == logical_bus:
                iface = item.get("iface")
                if not iface:
                    raise RuntimeError(
                        "%s 里 %s 的 iface 为空 —— 设备尚未置备" % (path, logical_bus))
                return iface
        raise RuntimeError("%s 里没有逻辑名为 %r 的 CAN 总线" % (path, logical_bus))
    raise RuntimeError("找不到 device.json（试过：%s）" % "、".join(_DEVICE_JSON_CANDIDATES))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if not argv or argv[0] in ("-h", "--help", "help"):
        parser.print_help()
        if not argv:
            # 不带子命令时优先进 TUI（若 hwtui 可用）。板上 stty size 返回 0 0，
            # 尺寸处理一律交给 hwtui.tui，本文件不去问终端大小。
            if _HWTUI_ERROR is None:
                _require_hwtui("TUI 主界面")
                return tui.run(tool_name="_model_", parser=parser)  # type: ignore[union-attr]
            sys.stderr.write("\nhwtui 尚不可用，TUI 未启用；命令行子命令仍可用。\n")
        return 0

    name = argv[0]
    if name not in _SUBCOMMANDS:
        parser.print_help()
        sys.stderr.write("\n未知子命令：%s\n" % name)
        return 2

    sub_parser = _SUBCOMMANDS[name]
    if any(a in ("-h", "--help") for a in argv[1:]):
        sub_parser.print_help()
        return 0

    # 交给子解析器时**去掉**子命令名：它已经作为主解析器的 dest 被消费过，
    # 留着会被当作位置参数。这一行是本文件唯一的 argparse 技巧。
    args = sub_parser.parse_args(argv[1:])
    args.command = name
    # 物理接口名按逻辑总线名解析一次；命令行给了就用手给的。
    if getattr(args, "interface", None) is None:
        logical = getattr(args, "bus", None) or _DEFAULT_BUS
        try:
            args.interface = resolve_interface(logical)
        except RuntimeError as exc:
            sys.stderr.write("无法解析内核接口名：%s\n" % exc)
            return 3
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
