"""Bounded image/GIF/video import. FFmpeg runs as a cancellable child, never via shell."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time
from PIL import Image, ImageOps, ImageSequence


class MediaLoader:
    VIDEO = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".mpeg", ".mpg"}

    @staticmethod
    def ffmpeg():
        executable = shutil.which("ffmpeg")
        if not executable:
            raise RuntimeError("FFmpeg no está disponible. Instálalo y agrega ffmpeg.exe al PATH para importar vídeo.")
        return executable

    @classmethod
    def load(cls, paths, fps=8, max_frames=120, step=1, start=0.0, duration=8.0, progress=None, cancel=None):
        if not 1 <= fps <= 30 or not 1 <= max_frames <= 256 or not 1 <= step <= 60:
            raise ValueError("FPS: 1–30, máximo de frames: 1–256, salto: 1–60.")
        if not 0 <= start <= 86400 or not 0.1 <= duration <= 120:
            raise ValueError("Inicio de vídeo: 0–86400 s; duración: 0.1–120 s.")
        frames = []
        for path in map(Path, paths):
            if cancel and cancel.is_set():
                raise InterruptedError("Importación cancelada.")
            remaining = max_frames - len(frames)
            if not remaining:
                break
            if path.suffix.lower() in cls.VIDEO:
                frames.extend(cls._video(path, fps, remaining, step, start, duration, progress, cancel))
            else:
                with Image.open(path) as image:
                    if image.width * image.height > 16_000_000:
                        raise ValueError(f"{path.name}: supera 16 megapíxeles.")
                    for index, frame in enumerate(ImageSequence.Iterator(image)):
                        if cancel and cancel.is_set():
                            raise InterruptedError("Importación cancelada.")
                        if index % step:
                            continue
                        frames.append(cls._cache(frame))
                        if progress:
                            progress(len(frames) / max_frames, f"{path.name}: {len(frames)} frames")
                        if len(frames) >= max_frames:
                            break
        if not frames:
            raise ValueError("La selección no contiene frames o el inicio supera la duración del vídeo.")
        return frames

    @staticmethod
    def _cache(frame):
        frame = ImageOps.exif_transpose(frame).convert("RGBA")
        frame.thumbnail((512, 256), Image.Resampling.LANCZOS)
        return frame

    @classmethod
    def _video(cls, path, fps, limit, step, start, duration, progress, cancel):
        with tempfile.TemporaryDirectory(prefix="flipper-video-") as folder:
            error_path = Path(folder) / "ffmpeg.log"
            command = [cls.ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                       "-protocol_whitelist", "file,pipe", "-threads", "2", "-ss", str(start), "-i", str(path.resolve()),
                       "-t", str(duration), "-map", "0:v:0", "-an", "-sn", "-dn",
                       "-vf", f"fps={fps}/{step},scale=512:256:force_original_aspect_ratio=decrease",
                       "-frames:v", str(limit), "-threads", "2", str(Path(folder) / "frame_%04d.png")]
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with error_path.open("wb") as errors:
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors, creationflags=flags)
                deadline = time.monotonic() + 90
                try:
                    while process.poll() is None:
                        if cancel and cancel.is_set():
                            raise InterruptedError("Importación de vídeo cancelada.")
                        if time.monotonic() > deadline:
                            raise RuntimeError("El vídeo tardó demasiado; reduce el fragmento.")
                        if progress:
                            count = len(list(Path(folder).glob("frame_*.png")))
                            progress(min(0.95, count / limit), f"Decodificando vídeo: {count}/{limit} frames")
                        if cancel:
                            cancel.wait(0.1)
                        else:
                            time.sleep(0.1)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
            if process.returncode:
                raise RuntimeError("FFmpeg: " + error_path.read_text(encoding="utf-8", errors="replace")[-1200:])
            frames = []
            for file in sorted(Path(folder).glob("frame_*.png")):
                if cancel and cancel.is_set():
                    raise InterruptedError("Importación cancelada.")
                with Image.open(file) as image:
                    frames.append(cls._cache(image))
            return frames


def export_gif(path, frames, fps, order=None, cancel=None):
    """Write the edited monochrome animation, without destroying an existing file on failure."""
    path = Path(path)
    sequence = order or tuple(range(len(frames)))
    if not sequence or not 1 <= fps <= 30:
        raise ValueError("No hay frames o FPS inválidos.")
    if cancel and cancel.is_set():
        raise InterruptedError("Exportación cancelada.")
    images = []
    for index in sequence:
        image = frames[index].convert("L").point(lambda value: 1 if value >= 128 else 0, "P")
        image.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
        images.append(image)
    handle, temporary = tempfile.mkstemp(prefix=".gif-", suffix=".gif", dir=path.parent)
    os.close(handle)
    try:
        images[0].save(temporary, format="GIF", save_all=True, append_images=images[1:],
                       duration=max(20, round(1000 / fps / 10) * 10), loop=0, disposal=2, background=1, optimize=False)
        if cancel and cancel.is_set():
            raise InterruptedError("Exportación cancelada.")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path
