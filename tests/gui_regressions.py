"""Native Tk regressions: python -m tests.gui_regressions (requires desktop)."""

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from flipper_asset_maker.gui import AppGUI, EditorState
from tests.gui_smoke import pump


class EditorRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One native root, just like the application. Recreating Tk roots in one
        # process leaves CustomTkinter's global DPI timers on old interpreters.
        cls.app = AppGUI()
        cls.callbacks = []
        cls.messages = []
        cls.app.report_callback_exception = lambda *error: cls.callbacks.append(error)
        cls.app._error = cls.messages.append
        cls.app.update()
        pump(cls.app, lambda: hasattr(cls.app, "ports") and not cls.app.busy)
        cls.defaults = {key: {name: variable.get() for name, variable in editor.items() if hasattr(variable, "numeric_field")}
                        for key, editor in cls.app.editors.items()}

    @classmethod
    def tearDownClass(cls):
        cls.app._close()
        cls.app.mainloop()

    def setUp(self):
        self.callbacks.clear()
        self.messages.clear()
        for key, editor in self.app.editors.items():
            self.app._stop_playback(key)
            self.app.cancel_gesture(key)
            editor.pop("pointer", None)
            editor["state"] = EditorState(revision=editor["state"].revision + 1)
            editor["text_box"].delete("1.0", "end")
            editor["text_box"].edit_reset()
            editor["tool"].set("Mover imagen")
            editor["tabs"].set("Imagen")
            for name, value in self.defaults[key].items():
                editor[name].set(value)
            self.app.render(key)
        self.app.show_page("assets")
        self.folder = tempfile.TemporaryDirectory()
        self.paths = []
        for index, color in enumerate(("black", "white", "gray")):
            path = Path(self.folder.name) / f"source_{index}.png"
            Image.new("RGB", (128, 64), color).save(path)
            self.paths.append(str(path))

    def tearDown(self):
        self.app.update()
        self.folder.cleanup()
        self.assertEqual(self.callbacks, [], "Unexpected Tk callback exception")

    def key(self, sequence, widget=None):
        widget = widget if widget is not None else self.app.editors["assets"]["canvas"]
        if getattr(widget, "_text_label", None) is not None:
            widget = widget._text_label
        editor = self.app.editors["assets"]
        if widget == editor["text_box"]._textbox:
            editor["tabs"].set("Texto")
        for name, variable in editor.items():
            field = getattr(variable, "numeric_field", None)
            if field is not None and widget == field.entry._entry:
                editor["tabs"].set("Texto" if name in {"font_size", "text_x", "text_y", "text_spacing"} else ("Dibujo" if name == "brush" else "Imagen"))
        self.app.focus_force()
        self.app.update()
        pump(self.app, lambda: widget.winfo_ismapped())
        widget.focus_set()
        self.app.update()
        self.assertEqual(self.app.focus_get(), widget, "Keyboard target must be visible and focused")
        widget.event_generate(sequence)
        self.app.update()

    def point(self, x=20, y=20):
        self.app.update()
        self.app.render("assets")
        ox, oy, scale = self.app.editors["assets"]["screen_rect"]
        return SimpleNamespace(x=ox+x*scale, y=oy+y*scale)

    def stroke(self):
        self.app.editors["assets"]["tool"].set("Lápiz")
        point = self.point()
        self.app.canvas_press("assets", point)
        self.app.canvas_release("assets", point)

    def test_drawing_history_shortcuts_work_from_canvas_and_controls(self):
        editor = self.app.editors["assets"]
        state = editor["state"]
        self.stroke()
        self.stroke()
        self.key("<Control-z>")
        self.assertEqual(len(state.strokes), 1)
        self.key("<Control-y>")
        self.assertEqual(len(state.strokes), 2)
        self.key("<Control-z>", self.app.nav["assets"])
        self.assertEqual(len(state.strokes), 1)
        self.key("<Control-Shift-Z>", self.app.nav["assets"])
        self.assertEqual(len(state.strokes), 2)
        self.assertEqual(editor["redo_button"].cget("state"), "disabled")
        self.assertEqual(editor["preview_undo"].cget("state"), "normal")

    def test_text_undo_redo_keeps_drawing_history_untouched(self):
        self.stroke()
        editor = self.app.editors["assets"]
        text = editor["text_box"]._textbox
        text.insert("1.0", "ABC")
        text.edit_separator()
        text.insert("end", "DEF")
        text.edit_separator()
        self.key("<Control-z>", text)
        self.assertEqual(text.get("1.0", "end-1c"), "ABC")
        self.assertEqual(len(editor["state"].strokes), 1)
        self.key("<Control-y>", text)
        self.assertEqual(text.get("1.0", "end-1c"), "ABCDEF")
        self.key("<Control-z>", text)
        self.key("<Control-Shift-Z>", text)
        self.assertEqual(text.get("1.0", "end-1c"), "ABCDEF")
        self.assertEqual(len(editor["state"].strokes), 1)

    def test_tool_keys_and_history_do_not_intercept_typing(self):
        editor = self.app.editors["assets"]
        self.key("<KeyPress-b>", editor["text_box"]._textbox)
        self.assertEqual(editor["text_box"].get("1.0", "end-1c"), "b")
        self.assertEqual(editor["tool"].get(), "Mover imagen")
        self.stroke()
        entry = editor["offset_x"].numeric_field.entry._entry
        self.key("<Control-z>", entry)
        self.assertEqual(len(editor["state"].strokes), 1)
        for symbol, tool in (("b", "Lápiz"), ("e", "Borrador"), ("w", "Blanco"), ("v", "Mover imagen"), ("t", "Mover texto")):
            self.key(f"<KeyPress-{symbol}>")
            self.assertEqual(editor["tool"].get(), tool)
        self.key("<KeyPress-bracketright>")
        self.assertEqual(editor["brush"].get(), 3)
        self.key("<KeyPress-bracketleft>")
        self.assertEqual(editor["brush"].get(), 2)

    def test_escape_and_undo_cancel_an_unfinished_stroke(self):
        self.stroke()
        editor = self.app.editors["assets"]
        point = self.point()
        self.app.canvas_press("assets", point)
        self.key("<Escape>")
        self.assertNotIn("stroke", editor)
        self.assertEqual(len(editor["state"].strokes), 1)
        self.app.canvas_press("assets", point)
        self.key("<Control-z>")
        self.assertNotIn("stroke", editor)
        self.assertEqual(len(editor["state"].strokes), 1)
        self.key("<Control-z>")
        self.assertEqual(editor["state"].strokes, [])

    def test_clear_can_be_undone_and_new_stroke_invalidates_redo(self):
        self.stroke()
        self.stroke()
        editor = self.app.editors["assets"]
        self.app.clear_drawing("assets")
        self.assertEqual(editor["state"].strokes, [])
        self.key("<Control-z>")
        self.assertEqual(len(editor["state"].strokes), 2)
        self.key("<Control-y>")
        self.assertEqual(editor["state"].strokes, [])
        self.key("<Control-z>")
        self.stroke()
        self.assertEqual(editor["state"].redo, [])
        self.assertEqual(editor["preview_redo"].cget("state"), "disabled")

    def test_frame_navigation_and_playback_shortcuts(self):
        self.app.convert("assets", tuple(self.paths))
        pump(self.app, lambda: not self.app.busy)
        editor = self.app.editors["assets"]
        self.key("<Home>")
        self.assertEqual(editor["state"].index, 0)
        self.assertEqual(editor["previous_button"].cget("state"), "disabled")
        self.key("<Next>")
        self.assertEqual(editor["state"].index, 1)
        self.key("<End>")
        self.assertEqual(editor["state"].index, 2)
        self.assertEqual(editor["next_button"].cget("state"), "disabled")
        self.key("<Prior>")
        self.assertEqual(editor["state"].index, 1)
        self.key("<space>")
        self.assertTrue(editor["state"].playing)
        self.key("<space>")
        self.assertFalse(editor["state"].playing)

    def test_brush_cursor_tracks_size_without_modifying_pixels(self):
        self.key("<KeyPress-b>")
        editor = self.app.editors["assets"]
        self.app.brush_cursor("assets", self.point())
        before = editor["canvas"].bbox("brush_cursor")
        self.assertIsNotNone(before)
        editor["brush"].set(8)
        after = editor["canvas"].bbox("brush_cursor")
        self.assertGreater(after[2]-after[0], before[2]-before[0])
        self.assertEqual(editor["state"].strokes, [])
        self.app.brush_cursor("assets", None)
        self.assertFalse(editor["canvas"].find_withtag("brush_cursor"))

    def test_escape_restores_position_before_drag(self):
        editor = self.app.editors["assets"]
        editor["offset_x"].set(-10)
        point = self.point()
        _ox, _oy, scale = editor["screen_rect"]
        self.app.canvas_press("assets", point)
        self.app.canvas_motion("assets", SimpleNamespace(x=point.x+15*scale, y=point.y))
        self.assertEqual(editor["offset_x"].get(), 5)
        self.key("<Escape>")
        self.assertEqual(editor["offset_x"].get(), -10)
        self.assertNotIn("drag", editor)

    def test_rapid_tab_changes_keep_the_latest_selection_visible(self):
        tabs = self.app.editors["assets"]["tabs"]
        tabs.set("Texto")
        tabs.set("Dibujo")
        tabs.set("Texto")
        complete = []
        self.app.after(180, lambda: complete.append(True))
        pump(self.app, lambda: complete)
        self.assertEqual(tabs.get(), "Texto")
        self.assertTrue(self.app.editors["assets"]["text_box"].winfo_ismapped())

    def test_empty_numeric_entries_do_not_reach_tk_numeric_variables(self):
        for key in ("assets", "animations"):
            editor = self.app.editors[key]
            for variable in editor.values():
                field = getattr(variable, "numeric_field", None)
                if field is None:
                    continue
                original = variable.get()
                field.entry.delete(0, "end")
                self.app.update()
                self.assertEqual(field.entry.get(), "")
                self.assertEqual(variable.get(), original)
                self.assertTrue(isinstance(field.slider.get(), (int, float)))
                field.entry._entry.event_generate("<FocusOut>")
                self.app.update()
                self.assertNotEqual(field.entry.get(), "")
                self.assertEqual(variable.get(), original)
        self.assertEqual(self.messages, [])

    def test_partial_signed_and_decimal_input(self):
        editor = self.app.editors["assets"]
        field = editor["offset_x"].numeric_field
        field.entry.delete(0, "end")
        field.entry.insert(0, "-")
        self.app.update()
        self.assertEqual(editor["offset_x"].get(), 0)
        field.entry.insert("end", "42")
        self.app.update()
        self.assertEqual(editor["offset_x"].get(), -42)
        contrast = editor["contrast"].numeric_field
        for draft, expected in ((".", 1.0), (".5", .5), ("1,7", 1.7), ("nan", 1.7), ("1e", 1.7)):
            contrast.entry.delete(0, "end")
            contrast.entry.insert(0, draft)
            self.app.update()
            self.assertAlmostEqual(editor["contrast"].get(), expected)
        contrast.entry._entry.event_generate("<FocusOut>")
        self.app.update()
        self.assertEqual(contrast.entry.get(), "1.7")

    def test_asset_picker_selects_multiple_and_appends_without_losing_existing(self):
        state = self.app.editors["assets"]["state"]
        with patch("flipper_asset_maker.gui.filedialog.askopenfilenames", return_value=tuple(self.paths[:2])) as picker:
            self.app.import_media("assets")
            pump(self.app, lambda: not self.app.busy)
            picker.assert_called_once()
        self.assertEqual(len(state.frames), 2)
        original = [frame.tobytes() for frame in state.frames]
        with patch("flipper_asset_maker.gui.filedialog.askopenfilenames", return_value=(self.paths[2],)):
            self.app.import_media("assets")
            pump(self.app, lambda: not self.app.busy)
        self.assertEqual(len(state.frames), 3)
        self.assertEqual([frame.tobytes() for frame in state.frames[:2]], original)
        self.assertEqual(state.index, 2)
        self.assertEqual(state.paths, tuple(self.paths))
        output = Path(self.folder.name) / "exports"
        output.mkdir()
        with patch("flipper_asset_maker.gui.filedialog.askdirectory", return_value=str(output)), patch("flipper_asset_maker.gui.messagebox.showinfo"):
            self.app.export_pack("assets")
            pump(self.app, lambda: not self.app.busy)
        archive = next(output.glob("*.zip"))
        with zipfile.ZipFile(archive) as files:
            self.assertEqual(len([name for name in files.namelist() if name.endswith(".bm")]), 3)
        self.assertEqual(self.messages, [])

    def test_replace_and_cancel_are_explicit_in_both_modules(self):
        for key in ("assets", "animations"):
            state = self.app.editors[key]["state"]
            with patch("flipper_asset_maker.gui.filedialog.askopenfilenames", return_value=tuple(self.paths[:2])):
                self.app.import_media(key)
                pump(self.app, lambda: not self.app.busy)
            with patch("flipper_asset_maker.gui.filedialog.askopenfilenames", return_value=()):
                self.app.import_media(key)
            self.assertEqual(len(state.frames), 2)
            with patch("flipper_asset_maker.gui.filedialog.askopenfilenames", return_value=(self.paths[2],)):
                self.app.import_media(key, replace=True)
                pump(self.app, lambda: not self.app.busy)
            self.assertEqual(len(state.frames), 1)
            self.assertEqual(state.paths, (self.paths[2],))
        self.assertEqual(self.messages, [])

    def test_failed_append_keeps_previous_frames_and_paths(self):
        self.app.convert("assets", tuple(self.paths[:2]))
        pump(self.app, lambda: not self.app.busy)
        state = self.app.editors["assets"]["state"]
        original = [frame.tobytes() for frame in state.frames]
        original_paths = state.paths
        self.app.convert("assets", (str(Path(self.folder.name)/"missing.png"),), append=True)
        pump(self.app, lambda: not self.app.busy)
        self.assertTrue(self.messages)
        self.assertEqual(state.paths, original_paths)
        self.assertEqual([frame.tobytes() for frame in state.frames], original)

    def test_import_with_empty_numeric_draft_restores_last_value(self):
        editor = self.app.editors["assets"]
        editor["offset_x"].set(-10)
        field = editor["offset_x"].numeric_field
        field.entry.delete(0, "end")
        self.app.update()
        with patch("flipper_asset_maker.gui.filedialog.askopenfilenames", return_value=tuple(self.paths[:2])):
            self.app.import_media("assets")
            pump(self.app, lambda: not self.app.busy)
        self.assertEqual(len(editor["state"].frames), 2)
        self.assertEqual(field.entry.get(), "-10")
        self.assertEqual(editor["offset_x"].get(), -10)
        self.assertEqual(self.messages, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
