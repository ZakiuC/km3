#!/usr/bin/env python3
"""Offline, real-curses PMP4010 acceptance; no action/read-start keys are sent.

Run from any directory with Python's isolated mode::

    python3 -I tools/hwtui/verify_pmp4010_pty.py

Raw terminal bytes, decoded screen snapshots, and assertion results are saved
under /tmp/edge-tui-pty. This uses only the standard library. The terminal
decoder is independent of hwtui: it interprets cursor addresses as terminal
columns and represents each CJK continuation cell separately.
"""
from __future__ import annotations

import argparse
import codecs
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time
import unicodedata


ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "device/drivers/pmp4010/tools/pmp4010.py"
KEYS = {"enter": b"\r", "esc": b"\x1b", "down": b"\x1bOB",
        "up": b"\x1bOA", "f1": b"\x1bOP", "pgdn": b"\x1b[6~",
        "pgup": b"\x1b[5~", "?": b"?", "q": b"q"}
VIEWS = (("Status", "状态概览", "本地配置"),
         ("Calibrate", "标定读取", "608F/6091"),
         ("Position", "位置运动", "PP 绝对目标"),
         ("Params", "参数维护", "EEPROM 保存"),
         ("Raw frames", "原始帧监听", "零发送"))


def width(char):
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


class Terminal:
    """Small VT100/xterm screen decoder, with UTF-8 and wide-cell handling.

    Supports the cursor, erase, mode, and scroll sequences used by ncurses and
    hwtui. Unknown sequences fail acceptance instead of silently corrupting the
    artwork. SGR is recorded separately because curses itself emits resets.
    """
    def __init__(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.cells = [[" "] * cols for _ in range(rows)]
        self.y = self.x = 0
        self.saved = (0, 0)
        self.pending = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.unknown = []
        self.sgr = []
        self.scroll = (0, rows - 1)

    def _erase_cell(self, y, x):
        row = self.cells[y]
        if row[x] is None and x:
            row[x - 1] = " "
        elif row[x] is not None and width(row[x][0]) == 2 and x + 1 < self.cols:
            row[x + 1] = " "
        row[x] = " "

    def _newline(self):
        top, bottom = self.scroll
        if self.y == bottom:
            del self.cells[top]
            self.cells.insert(bottom, [" "] * self.cols)
        else:
            self.y = min(self.rows - 1, self.y + 1)

    def _write(self, char):
        size = width(char)
        if not size:
            x = min(self.x - 1, self.cols - 1)
            if x >= 0:
                if self.cells[self.y][x] is None:
                    x -= 1
                self.cells[self.y][x] += char
            return
        if self.x + size > self.cols:
            self.x = 0
            self._newline()
        self._erase_cell(self.y, self.x)
        if size == 2:
            self._erase_cell(self.y, self.x + 1)
        self.cells[self.y][self.x] = char
        if size == 2:
            self.cells[self.y][self.x + 1] = None
        self.x += size

    def _csi(self, body, final):
        private = body.startswith(("?", ">", "!"))
        values = body.lstrip("?>!").split(";")
        nums = [int(v) if v else 0 for v in values]
        n = nums[0] or 1
        if final in "Hf":
            self.y = min(self.rows - 1, max(0, nums[0] - 1))
            self.x = min(self.cols - 1, max(0, (nums[1] if len(nums) > 1 else 1) - 1))
        elif final in "ABCD":
            if final == "A": self.y = max(0, self.y - n)
            if final == "B": self.y = min(self.rows - 1, self.y + n)
            if final == "C": self.x = min(self.cols - 1, self.x + n)
            if final == "D": self.x = max(0, self.x - n)
        elif final == "G":
            self.x = min(self.cols - 1, n - 1)
        elif final == "d":
            self.y = min(self.rows - 1, n - 1)
        elif final in "JK":
            mode = nums[0]
            if final == "J":
                start = 0 if mode in (1, 2, 3) else self.y * self.cols + min(self.x, self.cols - 1)
                end = self.rows * self.cols if mode in (0, 2, 3) else self.y * self.cols + min(self.x + 1, self.cols)
                for pos in range(start, end):
                    self._erase_cell(*divmod(pos, self.cols))
            else:
                start = 0 if mode in (1, 2) else min(self.x, self.cols)
                end = self.cols if mode in (0, 2) else min(self.x + 1, self.cols)
                for col in range(start, end): self._erase_cell(self.y, col)
        elif final == "m":
            self.sgr.append(body)
        elif final == "r" and not private:
            self.scroll = (max(0, nums[0] - 1), min(self.rows - 1, (nums[1] if len(nums) > 1 else self.rows) - 1))
            self.y = self.x = 0
        elif final == "s":
            self.saved = self.y, self.x
        elif final == "u":
            self.y, self.x = self.saved
        elif final in "hl" and (private or nums == [4]):
            pass  # cursor visibility, keypad, alternate screen, wrap mode
        elif final == "t":
            pass  # xterm window operations (curses save/restore title)
        else:
            self.unknown.append("CSI " + body + final)

    def feed(self, data):
        self.pending += self.decoder.decode(data)
        i = 0
        while i < len(self.pending):
            char = self.pending[i]
            if char == "\x1b":
                if i + 1 == len(self.pending): break
                following = self.pending[i + 1]
                if following == "[":
                    match = re.match(r"\x1b\[([0-?]*)([ -/]*)([@-~])", self.pending[i:])
                    if not match: break
                    body, intermediate, final = match.groups()
                    if intermediate:
                        self.unknown.append(repr(match.group()))
                    else:
                        self._csi(body, final)
                    i += len(match.group())
                    continue
                if following in "()":
                    if i + 2 == len(self.pending): break
                    i += 3  # ncurses charset selection; app uses Unicode/ASCII
                    continue
                if following in "=>78DM":
                    if following == "7": self.saved = self.y, self.x
                    if following == "8": self.y, self.x = self.saved
                    if following == "D": self._newline()
                    if following == "M": self.y = max(0, self.y - 1)
                    i += 2
                    continue
                self.unknown.append("ESC " + repr(following))
                i += 2
                continue
            if char == "\r": self.x = 0
            elif char in "\n\v\f": self._newline()
            elif char == "\b": self.x = max(0, self.x - 1)
            elif char == "\t": self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
            elif char in "\x07\x0e\x0f": pass
            elif ord(char) >= 32 and char != "\x7f": self._write(char)
            else: self.unknown.append(repr(char))
            i += 1
        self.pending = self.pending[i:]

    def lines(self):
        return ["".join(char for char in row if char is not None) for row in self.cells]


class Session:
    def __init__(self, out, name, size, forced=(30, 100)):
        self.out, self.name = out, name
        self.size = size
        rows, cols = size if all(size) else (forced or (24, 80))
        self.terminal = Terminal(rows, cols)
        self.raw = bytearray()
        self.snapshots = {}
        self.checks = []
        self.sent = []
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", *size, 0, 0))
        command = [sys.executable, "-I", str(ENTRY)]
        if forced:
            command += ["--rows", str(forced[0]), "--cols", str(forced[1])]
        self.command = command
        env = dict(os.environ, LANG="zh_CN.UTF-8", LC_ALL="zh_CN.UTF-8", TERM="xterm")
        env.pop("LINES", None)
        env.pop("COLUMNS", None)
        self.process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                        cwd=ROOT, env=env, start_new_session=True)
        os.close(slave)

    def read(self, duration=.35):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if not select.select([self.master], [], [], max(0, deadline - time.monotonic()))[0]:
                break
            try:
                data = os.read(self.master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO: break
                raise
            if not data: break
            self.raw.extend(data)
            self.terminal.feed(data)
        return self.terminal.lines()

    def send(self, key):
        # Deliberately closed key allowlist: F5/F6 and action confirmations are
        # absent. Only navigation/help, text ?, and ordinary edit cancellation.
        self.sent.append(key)
        os.write(self.master, KEYS[key])
        # ncurses waits ESCDELAY (normally 1000 ms) to distinguish a lone Esc
        # from a function-key prefix. Do not mistake that input timeout for a
        # failed return; retain the application's normal environment behavior.
        return self.read(1.2 if key == "esc" else .25)

    def check(self, condition, description):
        self.checks.append({"check": description, "passed": bool(condition)})
        print(("PASS " if condition else "FAIL ") + self.name + ": " + description, flush=True)
        if not condition:
            raise AssertionError(description)

    def capture(self, name):
        lines = self.terminal.lines()
        self.snapshots[name] = lines
        self.check(not self.terminal.unknown, name + " decoded without unsupported terminal escapes")
        self.check(all(sum(width(c) for c in line) == self.terminal.cols for line in lines),
                   name + " every decoded row occupies exactly the terminal column count")
        self.check(all(row[0] != " " and row[-1] not in (" ", None) for row in self.terminal.cells),
                   name + " left/right outer border stays in first/last terminal columns")
        return lines

    def finish(self):
        if self.process.poll() is None:
            self.send("q")
        if self.process.poll() is None:
            # A failed assertion may leave help or an editor open. Esc cancels
            # the draft/returns, then q exits without invoking an operation.
            self.send("esc")
            self.send("q")
        try:
            result = self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=2)
            result = self.process.returncode
        self.read(.1)
        os.close(self.master)
        (self.out / (self.name + ".raw")).write_bytes(self.raw)
        artwork = []
        for name, lines in self.snapshots.items():
            artwork.append("=== " + self.name + " / " + name + " ===\n" + "\n".join(lines))
        (self.out / (self.name + ".screens.txt")).write_text("\n\n".join(artwork) + "\n", encoding="utf-8")
        print("\n\n".join(artwork), flush=True)
        return {"name": self.name, "command": self.command, "ioctl_size": self.size,
                "screen_size": [self.terminal.rows, self.terminal.cols], "exit_code": result,
                "raw_bytes": len(self.raw), "curses_sgr_sequences": self.terminal.sgr,
                "unknown_escapes": self.terminal.unknown,
                "keys_sent": self.sent,
                "checks": self.checks}


def run_views(session):
    session.read(1.5)  # allow the automatic local-only status snapshot to settle
    session.capture("menu")
    for index, (view, label, help_marker) in enumerate(VIEWS):
        session.send("enter")
        # Use nonzero form focus so returning to a reset form cannot pass.
        if view in ("Calibrate", "Position", "Params"):
            session.send("down")
            session.send("down")
        before = session.capture(view)
        session.check(any(re.search(r"[\u4e00-\u9fff]", line) for line in before), view + " contains real Chinese glyphs")
        session.check("/ " + label in before[0], view + " localized title proves the view was activated")
        focused_label = {"Calibrate": "> 离线标定四项:", "Position": "> 加速度 ",
                         "Params": "> 备份路径:"}.get(view)
        if focused_label:
            session.check(focused_label in "\n".join(before), view + " nonzero field focus is visible before help")
        for entry, close in (("?", "esc"), ("f1", "q")):
            session.send(entry)
            help_screen = session.capture(view + "-help-" + entry)
            session.check(help_screen != before and "帮助" in "\n".join(help_screen), view + " " + entry + " opens help")
            session.check("帮助：" + label in "\n".join(help_screen)
                          and "当前视图：" + label in "\n".join(help_screen)
                          and help_marker in "\n".join(help_screen),
                          view + " help contains its own heading, context, and description")
            session.check(all(marker in "\n".join(help_screen) for marker in
                              ("F5 才可能发帧", "607Dh 等于没有", "用独立急停", "save 写 EEPROM 不可逆")),
                          view + " help displays all four safety warnings")
            session.send(close)
            session.check(session.terminal.lines() == before, view + " " + close + " restores exact view and focus")
        if view == "Position":
            session.send("?")
            help_top = session.terminal.lines()
            session.send("pgdn")
            help_down = session.capture("Position-help-scrolled")
            session.check(help_down != help_top, "PgDn scrolls overflowing help")
            session.send("pgup")
            session.check(session.terminal.lines() == help_top, "PgUp returns help to original top")
            session.send("esc")
        if view == "Calibrate":
            session.send("enter")  # selected offline-calibration text field
            edit_before = session.terminal.lines()
            session.send("?")
            edit_after = session.capture("Calibrate-edit-question")
            session.check(edit_after != edit_before and "> 离线标定四项: ?_" in "\n".join(edit_after),
                          "text edit consumes ? as an ordinary character in the selected field")
            session.check("帮助：" + label not in "\n".join(edit_after), "text edit ? does not open view-specific help")
            session.send("esc")
            session.check(session.terminal.lines() == before, "Esc cancels question-mark draft and preserves focus")
        session.send("esc")
        if index < 4:
            session.send("down")


def check_render_output():
    """Inspect app rendering separately from ncurses startup/reset SGR.

    No event loop/poll is run, so this part performs no status or bus I/O.
    Decode both full and incremental output independently and compare the
    terminal result to each complete app grid, including wide characters.
    """
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ENTRY.parent))
    import pmp4010
    from pmp4010_tui import PmpApp
    from tui import render

    options = pmp4010.build_parser().parse_args(["--rows", "30", "--cols", "100"])
    app = PmpApp(pmp4010, options)
    previous = None
    terminal = Terminal(30, 100)
    checked = 0
    byte_count = 0
    for view, _, _ in VIEWS:
        app.show_view(view)
        app.set_form(app.forms.get(view))
        for help_open in (False, True):
            if help_open:
                app.dispatch_key("?", ord("?"))
            lines = app.build().to_lines()
            for full in (True, False):
                output = "".join(render(None if full else previous, lines))
                if re.search(r"\x1b\[[0-?]*[ -/]*m", output):
                    raise AssertionError("SGR in app render: " + view)
                if full:
                    reference = Terminal(30, 100)
                    reference.feed(output.encode("utf-8"))
                else:
                    terminal.feed(output.encode("utf-8"))
                    reference = terminal
                if reference.unknown or reference.lines() != lines:
                    raise AssertionError("decoded app render disagrees with grid: " + view)
                checked += 1
                byte_count += len(output.encode("utf-8"))
            previous = lines
            if help_open:
                app.dispatch_key("esc", 27)
    print("PASS: {} full/incremental app renders, {} bytes, no SGR; independent terminal matches all grids".format(checked, byte_count))
    return {"checked_renders": checked, "output_bytes": byte_count,
            "sgr_sequences": 0, "decoded_grids_match": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/edge-tui-pty"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    failure = None
    render_result = None
    try:
        render_result = check_render_output()
    except AssertionError as exc:
        failure = str(exc)
    for name, size, forced, full in (
        ("30x100", (30, 100), (30, 100), True),
        ("24x80", (24, 80), (30, 100), True),
        ("zero-forced", (0, 0), (30, 100), False),
        ("zero-default", (0, 0), None, False),
    ):
        session = Session(args.output_dir, name, size, forced)
        try:
            if full:
                run_views(session)
            else:
                session.read(1.5)
                session.capture("fallback-menu")
                session.send("?")
                session.capture("fallback-help")
                session.check("帮助" in "\n".join(session.terminal.lines()), "zero-size fallback can open help")
                session.send("esc")
        except (AssertionError, OSError, UnicodeError) as exc:
            failure = str(exc)
            session.snapshots["failure"] = session.terminal.lines()
        finally:
            result = session.finish()
            results.append(result)
        if result["exit_code"] != 0:
            failure = failure or "child exited with code " + str(result["exit_code"])
        if failure:
            break
    report = {"locale": "zh_CN.UTF-8", "term": "xterm", "results": results,
              "app_render": render_result,
              "failure": failure, "scope": "offline PTY; no F5/F6/action keys; no hardware acceptance"}
    (args.output_dir / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Artifacts: " + str(args.output_dir))
    if failure:
        print("FAIL: " + failure)
        return 1
    print("PASS: real curses PTY navigation, help, editing, and terminal-size fallback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
