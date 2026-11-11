"""Build a fresh installable tree; never overwrite an existing pack implicitly."""

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
import zipfile

from .image_processor import ImageProcessor
from .models import AnimationSettings


@dataclass(frozen=True)
class ExportResult:
    root: Path
    archive: Path
    remote_parent: str
    frames: int


def build_meta(settings: AnimationSettings, count: int) -> str:
    sequence = settings.sequence(count)
    # Loop is native passive playback. Ping-pong is encoded in Frames order.
    return ("Filetype: Flipper Animation\nVersion: 1\n\nWidth: 128\nHeight: 64\n"
            f"Passive frames: {len(sequence)}\nActive frames: 0\n"
            f"Frames order: {' '.join(map(str, sequence))}\nActive cycles: 0\n"
            f"Frame rate: {settings.fps}\nDuration: {settings.duration}\n"
            "Active cooldown: 0\n\nBubble slots: 0\n")


class PackExporter:
    @staticmethod
    def export(destination, frames, settings, progress=None, cancel=None) -> ExportResult:
        meta = build_meta(settings, len(frames))
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        # Official uploads are isolated too: users merge manifest deliberately.
        momentum = settings.target in {"momentum", "momentum_legacy"}
        root_name = settings.pack if momentum else f"{settings.target}_{settings.pack}"
        root = destination / root_name
        archive = destination / f"{root_name}.zip"
        if root.exists() or archive.exists():
            raise FileExistsError("El pack o ZIP ya existe. Elige otro nombre o carpeta de salida.")
        stage = Path(tempfile.mkdtemp(prefix=".export-", dir=destination))
        try:
            staged_root = stage / root_name
            anims = staged_root / ("Anims" if settings.target == "momentum" else "anims") if momentum else staged_root
            animation = anims / settings.name
            animation.mkdir(parents=True)
            for index, frame in enumerate(frames):
                if cancel and cancel.is_set():
                    raise InterruptedError("Exportación cancelada.")
                if frame.size != (128, 64):
                    raise ValueError("Cada frame debe medir 128×64.")
                if settings.target == "firmware_source":
                    frame.save(animation / f"frame_{index}.png")
                else:
                    (animation / f"frame_{index}.bm").write_bytes(ImageProcessor.to_bm(frame))
                if progress:
                    progress((index + 1) / (len(frames) + 1), f"Exportando frame {index + 1}/{len(frames)}")
            (animation / "meta.txt").write_text(meta, encoding="utf-8", newline="\n")
            (anims / "manifest.txt").write_text(
                "Filetype: Flipper Animation Manifest\nVersion: 1\n\n"
                f"Name: {settings.name}\nMin butthurt: {settings.min_butthurt}\nMax butthurt: {settings.max_butthurt}\n"
                f"Min level: {settings.min_level}\nMax level: {settings.max_level}\nWeight: {settings.weight}\n", encoding="utf-8", newline="\n")
            staged_zip = stage / archive.name
            with zipfile.ZipFile(staged_zip, "w", zipfile.ZIP_DEFLATED) as bundle:
                for file in sorted(staged_root.rglob("*")):
                    if file.is_file():
                        relative = file.relative_to(staged_root)
                        prefix = f"asset_packs/{settings.pack}" if momentum else ("assets/dolphin/external" if settings.target == "firmware_source" else "dolphin")
                        bundle.write(file, f"{prefix}/{relative.as_posix()}")
            if cancel and cancel.is_set():
                raise InterruptedError("Exportación cancelada.")
            # On Windows rename fails when the destination exists.
            staged_root.rename(root)
            archive_created = False
            try:
                # Exclusive creation prevents clobbering a concurrent export.
                with archive.open("xb") as output, staged_zip.open("rb") as source:
                    archive_created = True
                    shutil.copyfileobj(source, output)
            except Exception:
                shutil.rmtree(root)
                if archive_created:
                    archive.unlink(missing_ok=True)
                raise
            if progress:
                progress(1.0, "Pack y ZIP exportados")
            return ExportResult(root, archive, "/ext/asset_packs" if momentum else "", len(frames))
        finally:
            shutil.rmtree(stage, ignore_errors=True)
