"""Regression tests for binary contracts, exports and the serial state machine."""

import hashlib
import io
from pathlib import Path
import re
import tempfile
import threading
import unittest
import zipfile

from PIL import Image, ImageOps

from flipper_asset_maker.image_processor import ImageProcessor
from flipper_asset_maker.models import AnimationSettings, ImageSettings
from flipper_asset_maker.pack_exporter import PackExporter, build_meta
from flipper_asset_maker.usb_manager import FlipperUSBError, FlipperUSBManager


class ImageTests(unittest.TestCase):
    def test_bm_matches_official_inverted_xbm_raster(self):
        image = Image.new("1", (128, 64), 255)
        for point in ((0, 0), (7, 0), (8, 0), (127, 63)):
            image.putpixel(point, 0)
        reference = io.BytesIO()
        ImageOps.invert(image).save(reference, format="XBM")
        # Same raster extraction as the official icon.py, independent of our codec.
        data = bytes(int(value, 16) for value in re.findall(rb"0x([0-9a-fA-F]{2})", reference.getvalue()))
        actual = ImageProcessor.to_bm(image)
        self.assertEqual(actual, b"\x00" + data)
        self.assertEqual(actual[:3], b"\x00\x81\x01")
        self.assertEqual(actual[-1], 128)
        self.assertEqual(len(actual), 1025)
        self.assertEqual(ImageProcessor.from_bm(actual).tobytes(), image.tobytes())

    def test_partial_row_alignment(self):
        image = Image.new("1", (9, 2), 255)
        image.putpixel((8, 0), 0)
        image.putpixel((0, 1), 0)
        data = ImageProcessor.to_bm(image)
        self.assertEqual(data, b"\x00\x00\x01\x01\x00")
        self.assertEqual(ImageProcessor.from_bm(data, (9, 2)).tobytes(), image.tobytes())

    def test_raw_and_compressed_payload_not_silently_accepted(self):
        for data in (bytes(1024), b"\x01" + bytes(1024), b"", bytes(1026)):
            with self.assertRaises(ValueError):
                ImageProcessor.from_bm(data)

    def test_transparency_fit_and_monochrome(self):
        source = Image.new("RGBA", (20, 80), (0, 0, 0, 0))
        for fit in ("contain", "cover", "stretch"):
            converted = ImageProcessor.process(source, ImageSettings(fit=fit))
            self.assertEqual(converted.mode, "1")
            self.assertEqual(converted.size, (128, 64))
            self.assertEqual(converted.getextrema(), (255, 255))

    def test_contain_centers_and_cover_fills(self):
        source = Image.new("RGB", (64, 64), "black")
        contained = ImageProcessor.process(source, ImageSettings(dither=False))
        self.assertEqual(contained.getpixel((0, 0)), 255)
        self.assertEqual(contained.getpixel((64, 32)), 0)
        covered = ImageProcessor.process(source, ImageSettings(fit="cover"))
        self.assertEqual(covered.getextrema(), (0, 0))

    def test_preview_uses_exact_colors_and_nearest_pixels(self):
        image = Image.new("1", (128, 64), 255)
        image.putpixel((0, 0), 0)
        preview = ImageProcessor.preview(image, 4)
        self.assertEqual(preview.getpixel((3, 3)), (0, 0, 0))
        self.assertEqual(preview.getpixel((4, 0)), (255, 140, 0))

    def test_gif_and_multiple_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Image.new("RGB", (128, 64), "black")
            second = Image.new("RGB", (128, 64), "white")
            gif = Path(folder) / "source.gif"
            png = Path(folder) / "last.png"
            first.save(gif, save_all=True, append_images=[second], duration=100)
            second.save(png)
            frames = ImageProcessor.load([gif, png], ImageSettings())
            self.assertEqual(len(frames), 3)
            self.assertEqual(frames[0].getextrema(), (0, 0))
            self.assertEqual(frames[1].getextrema(), (255, 255))

    def test_cancellation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.png"
            Image.new("RGB", (10, 10)).save(path)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(InterruptedError):
                ImageProcessor.load([path], ImageSettings(), cancel=cancel)

    def test_xbm_has_matching_native_raster(self):
        frame = Image.new("1", (128, 64), 255)
        frame.putpixel((5, 0), 0)
        xbm = ImageProcessor.to_xbm(frame, "2-test")
        self.assertIn("#define img_2_test_width 128", xbm)
        values = bytes(int(value, 16) for value in re.findall(r"0x([0-9a-f]{2})", xbm))
        self.assertEqual(values, ImageProcessor.to_bm(frame)[1:])


class ExportTests(unittest.TestCase):
    def test_pack_zip_structure_and_header(self):
        for target, prefix in (("momentum", "asset_packs/Test/Anims"), ("official", "dolphin")):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                settings = AnimationSettings(pack="Test", name="Example", target=target, playback="pingpong")
                frames = [Image.new("1", (128, 64), i % 2 * 255) for i in range(3)]
                result = PackExporter.export(folder, frames, settings)
                with zipfile.ZipFile(result.archive) as archive:
                    self.assertIn(f"{prefix}/manifest.txt", archive.namelist())
                    self.assertIn(b"Frames order: 0 1 2 1", archive.read(f"{prefix}/Example/meta.txt"))
                    self.assertIn(b"Passive frames: 4", archive.read(f"{prefix}/Example/meta.txt"))
                    self.assertEqual(len(archive.read(f"{prefix}/Example/frame_0.bm")), 1025)

    def test_no_overwrite_and_cancel_removes_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            frames = [Image.new("1", (128, 64))]
            settings = AnimationSettings(pack="Test")
            result = PackExporter.export(folder, frames, settings)
            original = result.archive.read_bytes()
            with self.assertRaises(FileExistsError):
                PackExporter.export(folder, frames, settings)
            self.assertEqual(result.archive.read_bytes(), original)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(InterruptedError):
                PackExporter.export(folder, frames, AnimationSettings(pack="Cancelled"), cancel=cancel)
            self.assertEqual({p.name for p in Path(folder).iterdir()}, {"Test", "Test.zip"})

    def test_order_validation_and_repetition(self):
        self.assertIn("Frames order: 1 0 1", build_meta(AnimationSettings(order=(1, 0, 1)), 2))
        with self.assertRaises(ValueError):
            build_meta(AnimationSettings(order=(2,)), 2)
        with self.assertRaises(ValueError):
            build_meta(AnimationSettings(pack="../bad"), 1)


class FakeSerial:
    """An independent byte-stream peer, with deliberately fragmented responses."""

    def __init__(self, **_):
        self.is_open = True
        self.buffer = bytearray()
        self.files = {}
        self.directories = {"/ext", "/ext/asset_packs"}
        self.commands = []
        self.pending = None
        self.fail_md5 = False
        self.bad_identity = False
        self.timeout_ready = False

    @property
    def in_waiting(self):
        return min(len(self.buffer), 7)

    def _reply(self, text):
        self.buffer.extend(text.encode() + b"\r\n>: ")

    def write(self, data):
        data = bytes(data)
        if self.pending:
            path, remaining = self.pending
            self.files[path] = self.files.get(path, b"") + data
            remaining -= len(data)
            if remaining < 0:
                raise AssertionError("Too many binary bytes")
            self.pending = (path, remaining) if remaining else None
            if not remaining:
                self._reply("")
            return len(data)
        if data == b"\x03\r":
            self._reply("Flipper CLI")
            return len(data)
        command = data.decode().strip()
        self.commands.append(command)
        if command == "device_info":
            self._reply("hardware_model : Other" if self.bad_identity else "hardware_model : Flipper Zero\r\nfirmware_version : test")
            return len(data)
        match = re.fullmatch(r'storage (\w+) "([^"]+)"(?: (\d+))?', command)
        if not match:
            raise AssertionError(command)
        op, path, size = match.groups()
        if op == "stat":
            self._reply("Directory" if path in self.directories else "Storage error: File/Dir does not exist")
        elif op == "mkdir":
            self.directories.add(path)
            self._reply("")
        elif op == "write_chunk":
            if not self.timeout_ready:
                self.pending = path, int(size)
                self.buffer.extend(b"Ready\r\n")
        elif op == "md5":
            self._reply("0" * 32 if self.fail_md5 else hashlib.md5(self.files[path]).hexdigest())
        elif op == "list":
            self._reply("\t[D] A folder\r\n\t[F] frame 1.bm 1025b")
        else:
            self._reply("Storage error: denied")
        return len(data)

    def read(self, count):
        count = min(count, 7)
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def close(self):
        self.is_open = False


class USBTests(unittest.TestCase):
    def setUp(self):
        self.peer = FakeSerial()
        self.manager = FlipperUSBManager(serial_factory=lambda **_: self.peer, timeout=0.05)

    def tearDown(self):
        self.manager.disconnect()

    def test_identity_checked_before_storage(self):
        self.peer.bad_identity = True
        with self.assertRaises(FlipperUSBError):
            self.manager.connect("FAKE")
        self.assertFalse(self.manager.connected)
        self.assertEqual(self.peer.commands, ["device_info"])

    def test_listing_names_with_spaces(self):
        self.manager.connect("FAKE")
        entries = self.manager.list_directory()
        self.assertEqual([(p.name, p.directory, p.size) for p in entries], [("A folder", True, 0), ("frame 1.bm", False, 1025)])

    def test_binary_upload_is_exact_and_checksums_verified(self):
        self.manager.connect("FAKE")
        with tempfile.TemporaryDirectory() as folder:
            local = Path(folder) / "Pack"
            (local / "Anims").mkdir(parents=True)
            data = bytes(range(256)) * 9 + b"\r\n>: \x00\xff"
            (local / "Anims" / "frame_0.bm").write_bytes(data)
            progress = []
            remote = self.manager.upload_directory(local, progress=lambda fraction, _: progress.append(fraction))
            self.assertEqual(self.peer.files[f"{remote}/Anims/frame_0.bm"], data)
            self.assertEqual(progress[-1], 1)
            self.assertEqual(len([c for c in self.peer.commands if "write_chunk" in c]), 3)
            with self.assertRaises(FileExistsError):
                self.manager.upload_directory(local)

    def test_checksum_mismatch_not_reported_as_success(self):
        self.manager.connect("FAKE")
        self.peer.fail_md5 = True
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Pack"
            path.mkdir()
            (path / "a.bm").write_bytes(b"binary")
            with self.assertRaisesRegex(FlipperUSBError, "Checksum"):
                self.manager.upload_directory(path)

    def test_ready_timeout_disconnects_session(self):
        self.manager.connect("FAKE")
        self.peer.timeout_ready = True
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Pack"
            path.mkdir()
            (path / "a.bm").write_bytes(b"binary")
            with self.assertRaisesRegex(FlipperUSBError, "Tiempo"):
                self.manager.upload_directory(path)
        self.assertFalse(self.manager.connected)
        self.assertEqual(self.peer.files, {})

    def test_paths_reject_command_injection_and_root_mutation(self):
        for path in ('/ext/test"\rstorage format /ext', "/ext/../int", "/int/file", "/ext", "/", "/ext/a\\b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.manager.path(path, writable=True)
        self.assertEqual(self.manager.path("/ext/A folder/file.bm", writable=True), "/ext/A folder/file.bm")


if __name__ == "__main__":
    unittest.main()
