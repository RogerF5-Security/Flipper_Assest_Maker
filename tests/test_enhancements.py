"""Regressions for direct editing, video import and real-world CLI variants."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile
from PIL import Image, ImageOps
import serial

from flipper_asset_maker.image_processor import ImageProcessor
from flipper_asset_maker.media_loader import MediaLoader, export_gif
from flipper_asset_maker.models import AnimationSettings, ImageSettings, Stroke
from flipper_asset_maker.pack_exporter import PackExporter
from flipper_asset_maker.usb_manager import FlipperUSBError, FlipperUSBManager
from flipper_asset_maker.validation import validate_path
from tests.test_core import FakeSerial


class EditingTests(unittest.TestCase):
    def test_zoom_offset_does_not_stretch_image(self):
        source = Image.new("RGB", (64, 64), "black")
        image = ImageProcessor.process(source, ImageSettings(zoom=0.5, offset_x=10, offset_y=5))
        black = ImageOps.invert(image).getbbox()
        self.assertEqual(black, (58, 21, 90, 53))

    def test_text_only_centered_and_bold(self):
        image = ImageProcessor.process(Image.new("RGB", (128, 64), "white"),
                ImageSettings(text="RogerF5", font_size=12, text_x=64, text_y=32, text_center=True, bold=True))
        box = ImageOps.invert(image).getbbox()
        self.assertIsNotNone(box)
        self.assertLess(abs((box[0]+box[2])/2-64), 3)
        self.assertLess(abs((box[1]+box[3])/2-32), 3)

    def test_eraser_restores_original_image_under_drawing(self):
        source = Image.new("RGB", (128, 64), "black")
        settings = ImageSettings(strokes=(Stroke("white", ((10, 10), (30, 10)), 7),
                                         Stroke("eraser", ((20, 10),), 3)))
        image = ImageProcessor.process(source, settings)
        self.assertEqual(image.getpixel((15, 10)), 255)
        self.assertEqual(image.getpixel((20, 10)), 0)
        self.assertEqual(image.getpixel((20, 1)), 0)

    def test_text_and_drawing_survive_bm_roundtrip(self):
        frame = ImageProcessor.process(Image.new("RGB", (128, 64), "white"),
            ImageSettings(text="F5", strokes=(Stroke("black", ((5, 50), (100, 50)), 2),)))
        self.assertEqual(frame.tobytes(), ImageProcessor.from_bm(ImageProcessor.to_bm(frame)).tobytes())

    def test_bad_edit_settings(self):
        for options in ({"zoom": float("nan")}, {"zoom": 9}, {"offset_x": 999}, {"font_size": 2}):
            with self.assertRaises(ValueError):
                ImageSettings(**options)

    def test_gif_export_preserves_order_and_loop(self):
        with tempfile.TemporaryDirectory() as folder:
            frames = [Image.new("1", (128, 64), 0), Image.new("1", (128, 64), 255)]
            path = export_gif(Path(folder)/"out.gif", frames, 10, (1, 0, 1))
            with Image.open(path) as gif:
                self.assertEqual(gif.n_frames, 3)
                self.assertEqual(gif.info["loop"], 0)
                self.assertEqual(gif.info["duration"], 100)
                self.assertEqual(gif.convert("L").getpixel((0, 0)), 255)
                gif.seek(1)
                self.assertEqual(gif.convert("L").getpixel((0, 0)), 0)

    def test_all_target_formats_and_manifest_settings_validate(self):
        with tempfile.TemporaryDirectory() as folder:
            for target in ("momentum", "momentum_legacy", "official", "firmware_source"):
                settings = AnimationSettings(pack=target, target=target, min_level=2, max_level=3, weight=7)
                result = PackExporter.export(folder, [Image.new("1", (128, 64))], settings)
                self.assertTrue(validate_path(result.root).ok)
                self.assertTrue(validate_path(result.archive).ok)
                with zipfile.ZipFile(result.archive) as archive:
                    manifest = next(name for name in archive.namelist() if name.endswith("manifest.txt"))
                    self.assertIn(b"Weight: 7", archive.read(manifest))

    def test_validator_detects_legacy_missing_bm_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            result = PackExporter.export(folder, [Image.new("1", (128, 64))], AnimationSettings())
            frame = next(result.root.rglob("*.bm"))
            frame.write_bytes(frame.read_bytes()[1:])
            report = validate_path(result.root)
            self.assertFalse(report.ok)
            self.assertIn("sin cabecera", report.to_text())

    def test_validator_accepts_selecting_single_animation(self):
        with tempfile.TemporaryDirectory() as folder:
            result = PackExporter.export(folder, [Image.new("1", (128, 64))], AnimationSettings())
            self.assertTrue(validate_path(next(result.root.rglob("meta.txt")).parent).ok)


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not available")
class VideoTests(unittest.TestCase):
    def test_real_video_trim_sampling_and_gif_export(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"source.mp4"
            subprocess.run([MediaLoader.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc=size=256x128:rate=10:duration=2", "-pix_fmt", "yuv420p", str(path)],
                            check=True, capture_output=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            sources = MediaLoader.load([path], fps=10, start=0.5, duration=1.0, max_frames=20, step=2)
            self.assertEqual(len(sources), 5)
            frames = [ImageProcessor.process(frame, ImageSettings(text="VIDEO")) for frame in sources]
            output = export_gif(Path(folder)/"edited.gif", frames, 5)
            with Image.open(output) as gif:
                self.assertEqual(gif.size, (128, 64))
                self.assertEqual(gif.n_frames, 5)

    def test_video_cancellation(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(InterruptedError):
            MediaLoader.load(["not-decoded.mp4"], cancel=cancelled)


class VariantSerial(FakeSerial):
    def __init__(self, variant):
        super().__init__()
        self.variant = variant

    def write(self, data):
        data = bytes(data)
        if data == b"\x03\r":
            self.buffer.extend(b"\r\n>: \r\n>: ")
            return len(data)
        command = data.decode(errors="replace").strip()
        if command in {"device_info", "info device", "!"}:
            self.commands.append(command)
            if self.variant == "fallback" and command == "device_info":
                self._reply("Unknown command")
            elif self.variant == "fallback":
                self._reply("\x1b[32m  hardware.model : Flipper Zero\x1b[0m\r\n  firmware.version : mntm-012")
            else:
                self._reply("  hardware_model      : Flipper Zero\r\n  firmware_version : 1.4.0")
            return len(data)
        return super().write(data)


class USBVariantTests(unittest.TestCase):
    def test_double_prompt_and_padded_keys(self):
        peer = VariantSerial("padded")
        manager = FlipperUSBManager(serial_factory=lambda **_: peer)
        try:
            manager.connect("FAKE")
            self.assertEqual(manager.device["hardware_model"], "Flipper Zero")
            self.assertIn("1.4.0", manager.identity)
            self.assertEqual(len(manager.list_directory()), 2)
        finally:
            manager.disconnect()

    def test_fallback_to_dotted_info_with_ansi(self):
        peer = VariantSerial("fallback")
        manager = FlipperUSBManager(serial_factory=lambda **_: peer)
        try:
            manager.connect("FAKE")
            self.assertEqual(manager.device["firmware_version"], "mntm-012")
            self.assertEqual(peer.commands[:2], ["device_info", "info device"])
        finally:
            manager.disconnect()

    def test_access_denied_is_actionable(self):
        def denied(**_):
            raise serial.SerialException("Access denied")
        manager = FlipperUSBManager(serial_factory=denied)
        with self.assertRaisesRegex(FlipperUSBError, "Cierra otras ventanas"):
            manager.connect("FAKE")


if __name__ == "__main__":
    unittest.main()
