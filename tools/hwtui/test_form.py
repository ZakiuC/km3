#!/usr/bin/env python3
"""Pure character-grid and keyboard-state checks; no terminal or bus access."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tui


def press(target, name, raw=None):
    return target.on_key(name, raw if raw is not None else (ord(name) if len(name) == 1 else 0))


class TestField(unittest.TestCase):
    def test_edit_is_draft_until_enter(self):
        field = tui.Field("path", "old")
        self.assertTrue(field.begin_edit())
        for char in "qj/":
            self.assertTrue(press(field, char))
        self.assertEqual(field.value, "old")
        self.assertEqual(field.text, "oldqj/")
        self.assertEqual(field.display(20), "oldqj/_")
        press(field, "enter")
        self.assertFalse(field.editing)
        self.assertEqual(field.value, "oldqj/")

    def test_escape_discards_draft(self):
        field = tui.Field("path", "original")
        field.begin_edit()
        press(field, "x")
        press(field, "esc")
        self.assertEqual((field.text, field.value, field.error), ("original", "original", ""))
        self.assertFalse(field.editing)

    def test_cursor_insertion_delete_backspace_and_bounds(self):
        field = tui.Field("text", "abc")
        field.begin_edit()
        press(field, "home")
        press(field, "left")
        press(field, "backspace")
        self.assertEqual(field.cursor, 0)
        press(field, "right")
        press(field, "X")
        self.assertEqual(field.text, "aXbc")
        press(field, "delete")
        self.assertEqual(field.text, "aXc")
        press(field, "backspace")
        self.assertEqual(field.text, "ac")
        press(field, "end")
        press(field, "right")
        press(field, "delete")
        self.assertEqual(field.cursor, 2)
        self.assertEqual(field.text, "ac")

    def test_numeric_rejects_invalid_empty_and_nonfinite(self):
        for bad in ("", "+", "hello", "NaN", "sNaN", "Infinity", "-Infinity"):
            with self.subTest(bad=bad):
                field = tui.Field("number", bad, kind="number")
                field.begin_edit()
                self.assertFalse(field.commit())
                self.assertTrue(field.editing)
                self.assertEqual(field.error, "finite number required")

    def test_numeric_commits_finite_decimal_without_device_policy(self):
        for value in ("0", "-12.5", "+.5", "1e-4", " 42 "):
            with self.subTest(value=value):
                field = tui.Field("number", "", kind="number")
                field.begin_edit()
                for char in value:
                    press(field, char)
                self.assertTrue(field.commit())
                self.assertEqual(field.value, value)

    def test_numeric_failure_can_be_corrected_or_cancelled(self):
        field = tui.Field("number", "1", kind="number")
        field.begin_edit()
        press(field, "j")
        press(field, "enter")
        self.assertTrue(field.error)
        self.assertEqual(field.value, "1")
        press(field, "backspace")
        self.assertEqual(field.error, "")
        press(field, "enter")
        self.assertFalse(field.editing)
        self.assertEqual(field.value, "1")

    def test_readonly_cannot_start_editing(self):
        field = tui.Field("state", "ready", kind="readonly")
        self.assertFalse(field.begin_edit())
        self.assertFalse(press(field, "j"))
        self.assertEqual(field.display(30), "ready [ro]")

    def test_cursor_stays_visible_when_value_scrolls(self):
        field = tui.Field("name", "abcdefgh")
        field.begin_edit()
        self.assertEqual(field.display(4), "fgh_")
        press(field, "home")
        self.assertEqual(field.display(4), "_abc")
        press(field, "right")
        self.assertEqual(field.display(1), "_")
        self.assertEqual(field.display(0), "")

    def test_unsupported_input_is_consumed_without_insertion(self):
        field = tui.Field("name", "")
        field.begin_edit()
        for name, raw in (("中", 20013), ("", 1), ("f5", 0), ("\x1b", 27)):
            self.assertTrue(press(field, name, raw))
        self.assertEqual(field.text, "")
        self.assertFalse(press(field, "", 3))

    def test_input_length_is_bounded(self):
        field = tui.Field("name", max_length=3)
        field.begin_edit()
        for char in "abcde":
            press(field, char)
        self.assertEqual(field.text, "abc")

    def test_bad_kind_is_explicit_error(self):
        with self.assertRaises(ValueError):
            tui.Field("x", kind="other")


class TestForm(unittest.TestCase):
    def test_navigation_clamps_and_tab_selects(self):
        form = tui.Form([tui.Field("first"), tui.Field("second")])
        press(form, "up")
        self.assertEqual(form.selected, 0)
        press(form, "tab")
        press(form, "down")
        self.assertEqual(form.selected, 1)
        press(form, "k")
        self.assertEqual(form.selected, 0)

    def test_editing_owns_navigation_and_global_letters(self):
        form = tui.Form([tui.Field("first"), tui.Field("second")])
        press(form, "enter")
        for key in ("q", "j", "/", "down", "tab", "f5"):
            self.assertTrue(press(form, key))
        self.assertEqual(form.selected, 0)
        self.assertEqual(form.current.text, "qj/")
        self.assertEqual(form.values, {"first": "", "second": ""})
        press(form, "enter")
        self.assertEqual(form.values, {"first": "qj/", "second": ""})

    def test_exact_grid_snapshot_and_sanitized_readonly(self):
        form = tui.Form([tui.Field("path", "ab"), tui.Field("state", "中\x1b", kind="readonly")])
        press(form, "enter")
        grid = tui.Grid(2, 22)
        form.render(grid, tui.Rect(0, 0, 2, 22), "ascii")
        self.assertEqual(grid.to_lines(), ["> path: ab_           ", "  state: ?. [ro]      "])

    def test_invalid_number_keeps_visible_cursor_and_error_marker(self):
        form = tui.Form([tui.Field("n", "x", kind="number")])
        press(form, "enter")
        press(form, "enter")
        grid = tui.Grid(1, 12)
        form.render(grid, tui.Rect(0, 0, 1, 12))
        self.assertEqual(grid.to_lines(), ["x n: x_     "])
        self.assertTrue(form.editing)

    def test_scroll_keeps_selected_field_visible(self):
        form = tui.Form([tui.Field(str(i), i) for i in range(10)])
        for _ in range(20):
            press(form, "down")
        grid = tui.Grid(2, 15)
        form.render(grid, tui.Rect(0, 0, 2, 15))
        self.assertEqual(form.offset, 8)
        self.assertEqual(grid.to_lines(), ["  8: 8         ", "> 9: 9         "])

    def test_empty_and_degenerate_forms_are_safe(self):
        for rows, cols in ((0, 0), (1, 1), (24, 80), (40, 160)):
            for fields in ([], [tui.Field("name", "long value")]):
                with self.subTest(rows=rows, cols=cols, fields=len(fields)):
                    form = tui.Form(fields)
                    grid = tui.Grid(rows, cols)
                    form.render(grid, tui.Rect(0, 0, rows, cols))
                    self.assertEqual(len(grid.to_lines()), rows)
                    self.assertTrue(all(len(line) == cols for line in grid.to_lines()))
        self.assertFalse(press(tui.Form([]), "enter"))

    def test_duplicate_names_are_rejected(self):
        with self.assertRaises(ValueError):
            tui.Form([tui.Field("a"), tui.Field("a")])


class TestViewNavigation(unittest.TestCase):
    def make_app(self):
        app = tui.TuiApp(rows=24, cols=80)
        form = tui.Form([tui.Field("path", "")])
        app.set_views({"Status": None, "Position": form})
        return app, form

    def test_menu_enter_and_escape_preserve_selection(self):
        app, form = self.make_app()
        self.assertFalse(app.dispatch_key("down", 0))
        self.assertEqual(app.menu.selected, 1)
        self.assertFalse(app.dispatch_key("enter", 10))
        self.assertEqual(app.current_view, "Position")
        self.assertIs(app.focused_form, form)
        self.assertFalse(app.dispatch_key("esc", 27))
        self.assertIsNone(app.current_view)
        self.assertIsNone(app.focused_form)
        self.assertEqual(app.menu.selected, 1)

    def test_escape_cancels_edit_before_returning_to_menu(self):
        app, form = self.make_app()
        app.show_view("Position")
        app.dispatch_key("enter", 10)
        for char in "qj/":
            self.assertFalse(app.dispatch_key(char, ord(char)))
        self.assertEqual(form.current.text, "qj/")
        self.assertEqual(app.current_view, "Position")
        app.dispatch_key("esc", 27)
        self.assertEqual(form.current.value, "")
        self.assertEqual(app.current_view, "Position")
        app.dispatch_key("esc", 27)
        self.assertIsNone(app.current_view)
        self.assertTrue(app.dispatch_key("q", ord("q")))

    def test_ctrl_c_exits_with_form_focus(self):
        app, form = self.make_app()
        app.show_view("Position")
        app.dispatch_key("enter", 10)
        self.assertTrue(form.editing)
        self.assertTrue(app.dispatch_key("", 3))

    def test_focus_precedes_subclass_actions(self):
        class App(tui.TuiApp):
            def on_key(self, name, key):
                if super().on_key(name, key):
                    return True
                self.action = name
                return False
        app = App()
        app.set_form(tui.Form([tui.Field("confirm")]))
        app.dispatch_key("enter", 10)
        app.dispatch_key("q", ord("q"))
        app.dispatch_key("f5", 0)
        self.assertFalse(hasattr(app, "action"))
        self.assertEqual(app.focused_form.current.text, "q")
        app.dispatch_key("enter", 10)
        app.dispatch_key("f5", 0)
        self.assertEqual(app.action, "f5")

    def test_replacing_focus_or_view_cancels_previous_draft(self):
        app, form = self.make_app()
        app.show_view("Position")
        app.dispatch_key("enter", 10)
        app.dispatch_key("x", ord("x"))
        app.show_view("Status")
        self.assertFalse(form.editing)
        self.assertEqual(form.current.value, "")
        app.set_form(form)
        app.dispatch_key("enter", 10)
        app.set_form(None)
        self.assertFalse(form.editing)

    def test_empty_menu_and_unregistered_views(self):
        app = tui.TuiApp()
        app.set_views({"Status": None}, tui.Menu([]))
        self.assertFalse(app.dispatch_key("enter", 10))
        with self.assertRaises(KeyError):
            app.show_view("missing")

    def test_old_apps_keep_escape_quit_and_demo_enter_log(self):
        app = tui.TuiApp()
        self.assertTrue(app.dispatch_key("esc", 27))
        demo = tui._DemoApp()
        self.assertFalse(demo.dispatch_key("enter", 10))
        self.assertIsNone(demo.current_view)

    def test_loop_restores_cursor_when_poll_raises(self):
        if tui.curses is None:
            self.skipTest("curses unavailable")
        app = tui.TuiApp()
        with patch.object(app, "_install_winch"), patch.object(app, "_poll_size_change", return_value=False), \
                patch.object(app, "poll", side_effect=RuntimeError("poll failed")), \
                patch.object(tui.curses, "curs_set") as cursor:
            with self.assertRaisesRegex(RuntimeError, "poll failed"):
                app._main(object())
        self.assertEqual([call.args for call in cursor.call_args_list], [(0,), (1,)])
        self.assertFalse(app._running)

    def test_loop_flushes_curses_before_ansi_and_restores_signal_on_ctrl_c(self):
        if tui.curses is None:
            self.skipTest("curses unavailable")
        events = []

        class Screen:
            def keypad(self, enabled):
                pass

            def timeout(self, millis):
                pass

            def refresh(self):
                events.append("refresh")

            def untouchwin(self):
                events.append("untouch")

            def getch(self):
                events.append("getch")
                raise KeyboardInterrupt()

        app = tui.TuiApp()
        previous = tui.signal.SIG_DFL
        with patch.object(tui.signal, "getsignal", return_value=previous), \
                patch.object(tui.signal, "signal") as install, \
                patch.object(tui.curses, "curs_set") as cursor, \
                patch.object(app, "build", return_value=tui.Grid(1, 1)), \
                patch.object(app, "_write", side_effect=lambda data: events.append("ansi")):
            with self.assertRaises(KeyboardInterrupt):
                app._main(Screen())
        self.assertEqual(events, ["refresh", "untouch", "ansi", "getch"])
        self.assertEqual(install.call_args_list[-1].args, (tui.signal.SIGWINCH, previous))
        self.assertEqual([call.args for call in cursor.call_args_list], [(0,), (1,)])
        self.assertFalse(app._winch_installed)
        self.assertFalse(app._running)

    def test_resize_notification_queued_by_resizeterm_does_not_starve_quit(self):
        if tui.curses is None:
            self.skipTest("curses unavailable")

        class Screen:
            def __init__(self):
                self.size = (36, 140)
                self.pending = [tui.curses.KEY_RESIZE, ord("q")]
                self.reads = 0

            def keypad(self, enabled):
                pass

            def timeout(self, millis):
                pass

            def refresh(self):
                pass

            def untouchwin(self):
                pass

            def clearok(self, enabled):
                pass

            def getmaxyx(self):
                return self.size

            def getch(self):
                self.reads += 1
                if self.reads > 4:
                    raise AssertionError("recursive resize events starved q")
                return self.pending.pop(0)

            def resize(self, rows, cols):
                self.size = (rows, cols)
                # Model ncurses: a resizeterm call puts KEY_RESIZE before q.
                self.pending.insert(0, tui.curses.KEY_RESIZE)

        screen = Screen()
        app = tui.TuiApp()
        app.rows, app.cols = 36, 140
        with patch.object(tui, "resolve_size", return_value=(24, 80)), \
                patch.object(tui.curses, "resizeterm", side_effect=screen.resize) as resize, \
                patch.object(tui.curses, "resize_term", side_effect=screen.resize) as fallback, \
                patch.object(tui.curses, "update_lines_cols"), \
                patch.object(tui.curses, "curs_set"), \
                patch.object(app, "_install_winch"), \
                patch.object(app, "_poll_size_change", return_value=False), \
                patch.object(app, "_write") as write, \
                patch.object(app, "build", side_effect=lambda: tui.Grid(app.rows, app.cols)):
            self.assertEqual(app._main(screen), 0)
        resize.assert_called_once_with(24, 80)
        fallback.assert_not_called()
        self.assertEqual((app.rows, app.cols), (24, 80))
        self.assertEqual(screen.reads, 3)
        self.assertEqual(write.call_count, 2)

    def test_curses_already_resized_only_updates_cached_layout(self):
        if tui.curses is None:
            self.skipTest("curses unavailable")
        from unittest.mock import Mock
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        app = tui.TuiApp()
        app.rows, app.cols = 36, 140
        with patch.object(tui, "resolve_size", return_value=(24, 80)), \
                patch.object(tui.curses, "resizeterm") as resize, \
                patch.object(tui.curses, "resize_term") as fallback:
            self.assertTrue(app._resize_now(screen))
            self.assertFalse(app._resize_now(screen))
        resize.assert_not_called()
        fallback.assert_not_called()
        screen.clearok.assert_called_once_with(True)
        self.assertEqual((app.rows, app.cols), (24, 80))


if __name__ == "__main__":
    unittest.main()
