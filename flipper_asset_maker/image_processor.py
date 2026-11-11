"""Pillow pipeline and Flipper's uncompressed icon bitmap codec.

BM is a 0x00 encoding flag followed by XBM raster bytes, LSB first,
one bit per black pixel. Dimensions live in meta.txt, not in the BM file.
"""

from pathlib import Path
import re
import os
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps, ImageSequence

from .models import ImageSettings


class ImageProcessor:
    SIZE = (128, 64)
    MAX_FRAMES = 256
    MAX_SOURCE_PIXELS = 16_000_000

    @classmethod
    def process(cls, source: Image.Image, settings: ImageSettings) -> Image.Image:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        background = Image.new("RGBA", image.size, "white")
        background.alpha_composite(image)
        gray = background.convert("L")
        if settings.fit in {"contain", "cover"}:
            ratio = (min if settings.fit == "contain" else max)(128 / gray.width, 64 / gray.height)
            size = (max(1, round(gray.width * ratio * settings.zoom)), max(1, round(gray.height * ratio * settings.zoom)))
        else:
            size = (max(1, round(128 * settings.zoom)), max(1, round(64 * settings.zoom)))
        # Avoid enormous intermediate images for extremely thin panoramic input.
        if max(size) > 8192 or size[0] * size[1] > 8_000_000:
            raise ValueError("La escala produce una imagen demasiado grande. Reduce el tamaño o usa Contener.")
        resized = gray.resize(size, Image.Resampling.LANCZOS)
        gray = Image.new("L", cls.SIZE, 255)
        gray.paste(resized, ((128 - size[0]) // 2 + settings.offset_x, (64 - size[1]) // 2 + settings.offset_y))
        gray = ImageEnhance.Contrast(gray).enhance(settings.contrast)
        if settings.strokes:
            layer = Image.new("RGBA", cls.SIZE)
            draw = ImageDraw.Draw(layer)
            colors = {"black": (0, 0, 0, 255), "white": (255, 255, 255, 255), "eraser": (0, 0, 0, 0)}
            for stroke in settings.strokes:
                if not stroke.points:
                    continue
                color = colors[stroke.tool]
                if len(stroke.points) > 1:
                    draw.line(stroke.points, fill=color, width=stroke.width, joint="curve")
                radius = (stroke.width - 1) / 2
                for x, y in stroke.points:
                    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
            gray.paste(layer.convert("L"), (0, 0), layer.getchannel("A"))
        if settings.text:
            font = cls.load_font(settings.font, settings.font_size)
            draw = ImageDraw.Draw(gray)
            kwargs = dict(font=font, spacing=settings.text_spacing, stroke_width=1 if settings.bold else 0)
            box = draw.multiline_textbbox((0, 0), settings.text, **kwargs)
            x, y = settings.text_x - box[0], settings.text_y - box[1]
            if settings.text_center:
                x -= (box[2] - box[0]) // 2
                y -= (box[3] - box[1]) // 2
            color = 255 if settings.text_white else 0
            draw.multiline_text((x, y), settings.text, fill=color, stroke_fill=color, **kwargs)
        if settings.invert:
            gray = ImageOps.invert(gray)
        if settings.dither:
            return gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        return gray.point(lambda value: 255 if value >= settings.threshold else 0, "1")

    @staticmethod
    def load_font(family, size):
        name = {"Segoe UI": "segoeui.ttf", "Consolas": "consola.ttf", "Arial": "arial.ttf", "Pixel": None}[family]
        if name:
            for candidate in (Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name, name, "DejaVuSans.ttf"):
                try:
                    return ImageFont.truetype(str(candidate), size)
                except OSError:
                    pass
        return ImageFont.load_default(size=size)

    @classmethod
    def load(cls, paths, settings: ImageSettings, progress=None, cancel=None) -> list[Image.Image]:
        frames = []
        paths = tuple(Path(p) for p in paths)
        for file_index, path in enumerate(paths):
            with Image.open(path) as source:
                if source.width * source.height > cls.MAX_SOURCE_PIXELS:
                    raise ValueError(f"{path.name}: supera 16 megapíxeles.")
                for frame in ImageSequence.Iterator(source):
                    if cancel and cancel.is_set():
                        raise InterruptedError("Procesamiento cancelado.")
                    if len(frames) >= cls.MAX_FRAMES:
                        raise ValueError("Máximo 256 fotogramas por proyecto; recorta el GIF o la selección.")
                    frames.append(cls.process(frame.copy(), settings))
                    if progress:
                        progress((file_index + 1) / max(len(paths), 1), f"{path.name}: frame {len(frames)}")
        if not frames:
            raise ValueError("No se cargaron imágenes.")
        return frames

    @staticmethod
    def raster(image: Image.Image) -> bytes:
        if image.mode != "1":
            raise ValueError("El codec requiere una imagen monocroma de 1 bit.")
        pixels = image.load()
        data = bytearray()
        for y in range(image.height):
            for start in range(0, image.width, 8):
                byte = 0
                for bit in range(min(8, image.width - start)):
                    if pixels[start + bit, y] == 0:
                        byte |= 1 << bit
                data.append(byte)
        return bytes(data)

    @classmethod
    def to_bm(cls, image: Image.Image) -> bytes:
        return b"\x00" + cls.raster(image)

    @staticmethod
    def from_bm(data: bytes, size=(128, 64)) -> Image.Image:
        width, height = size
        if width < 1 or height < 1 or width > 128 or height > 64:
            raise ValueError("Dimensiones BM inválidas.")
        expected = ((width + 7) // 8) * height
        if len(data) != expected + 1 or data[0] != 0:
            raise ValueError("Se esperaba BM sin compresión: cabecera 00 + raster. No es un raster crudo.")
        return Image.frombytes("1", size, data[1:], "raw", "1;R", ((width + 7) // 8), 1).point(lambda p: 255 - p)

    @classmethod
    def to_xbm(cls, image: Image.Image, name="flipper") -> str:
        identifier = re.sub(r"\W", "_", name, flags=re.ASCII)
        if not identifier or identifier[0].isdigit():
            identifier = "img_" + identifier
        data = cls.raster(image)
        rows = [", ".join(f"0x{v:02x}" for v in data[i:i + 12]) for i in range(0, len(data), 12)]
        return (f"#define {identifier}_width {image.width}\n"
                f"#define {identifier}_height {image.height}\n"
                f"static unsigned char {identifier}_bits[] = {{\n  " + ",\n  ".join(rows) + "\n};\n")

    @staticmethod
    def preview(image: Image.Image, scale=4) -> Image.Image:
        return ImageOps.colorize(image.convert("L"), "black", "#FF8C00").resize(
            (image.width * scale, image.height * scale), Image.Resampling.NEAREST)
