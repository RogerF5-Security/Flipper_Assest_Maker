"""Run explicitly with python -m tests.gui_smoke; requires a Windows desktop."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import time
from PIL import Image
from flipper_asset_maker.gui import AppGUI


def pump(app, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.update()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("GUI operation timed out")


def main():
    app = AppGUI()
    failures = []
    app.report_callback_exception = lambda *error: failures.append(error)
    try:
        app.update()
        pump(app, lambda: hasattr(app, "ports") and not app.busy)
        with tempfile.TemporaryDirectory() as folder:
            assets = app.editors["assets"]
            assets["text_box"].insert("1.0", "RogerF5")
            app.center_text("assets")
            pump(app, lambda: assets["state"].preview is not None)
            app.convert("assets")
            pump(app, lambda: not app.busy)
            assert assets["state"].frames and not assets["state"].dirty
            source = Path(folder) / "source.gif"
            Image.new("RGB", (200, 100), "black").save(source, save_all=True,
                append_images=[Image.new("RGB", (200, 100), "white")], duration=100)
            app.show_page("animations")
            app.convert("animations", (str(source),))
            pump(app, lambda: not app.busy)
            editor = app.editors["animations"]
            assert len(editor["state"].frames) == 2
            assert len(editor["state"].previews) == 2
            assert editor["canvas"].find_all()
            app.select_frame("animations", 1)
            assert editor["state"].index == 1
            editor["playback"].set("Ida y vuelta")
            app.toggle_play("animations")
            pump(app, lambda: editor["state"].playing)
            app._stop_playback("animations")
            editor["threshold"].set("100")
            assert editor["state"].dirty
            # Drag the image by exact screen pixels at the current preview zoom.
            app.update()
            app.render("animations")
            ox, oy, scale = editor["screen_rect"]
            point = SimpleNamespace(x=ox+64*scale, y=oy+32*scale)
            moved = SimpleNamespace(x=point.x+10*scale, y=point.y-4*scale)
            app.canvas_press("animations", point)
            app.canvas_motion("animations", moved)
            app.canvas_release("animations", moved)
            assert editor["offset_x"].get() == 10 and editor["offset_y"].get() == -4
            app.center_image("animations")
            assert editor["offset_x"].get() == editor["offset_y"].get() == 0
            app.adjust_zoom("animations", 5)
            assert editor["zoom"].get() == 105
            app.reset_transform("animations")
            editor["tool"].set("Mover texto")
            app.center_text("animations")
            app.nudge("animations", 0, -1, 8)
            assert editor["text_y"].get() == 24
            editor["tool"].set("Lápiz")
            app.canvas_press("animations", point)
            app.canvas_motion("animations", moved)
            app.canvas_release("animations", moved)
            assert len(editor["state"].strokes) == 1
            app.undo_drawing("animations")
            assert not editor["state"].strokes
            app.undo_drawing("animations", True)
            assert len(editor["state"].strokes) == 1
            editor["text_box"].insert("1.0", "GIF")
            app.mark_dirty("animations")
            output = Path(folder) / "edited.gif"
            # Export must compose edits automatically, without an Apply click.
            with patch("flipper_asset_maker.gui.filedialog.asksaveasfilename", return_value=str(output)), patch("flipper_asset_maker.gui.messagebox.showinfo"):
                app.export_animation_gif("animations")
                pump(app, lambda: output.exists() and not app.busy)
            assert not editor["state"].dirty
            app.convert("animations")
            editor["threshold"].set("99")
            pump(app, lambda: not app.busy)
            assert editor["state"].dirty
            for size in ("1040x700", "1360x900"):
                app.geometry(size)
                for key in ("assets", "animations", "usb"):
                    app.show_page(key)
                    app.update()
                    assert app.pages[key].winfo_ismapped()
                    if key in app.editors:
                        canvas = app.editors[key]["canvas"]
                        assert canvas.winfo_rootx() + canvas.winfo_width() <= app.winfo_rootx() + app.winfo_width(), "Preview overflows the window"
            assert not failures, failures
        print("GUI_SMOKE_OK: text-only, drag, center, zoom, nudge, drawing undo/redo, live preview, automatic GIF export, resize, shutdown")
    finally:
        app._close()
        app.mainloop()


if __name__ == "__main__":
    main()
