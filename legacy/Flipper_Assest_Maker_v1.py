"""Flipper Asset Maker Pro.

GUI and CLI utility to create and validate Flipper Zero animation asset packs.
The generated files follow the Flipper animation manifest/meta structure and
write 1-bit .bm frames sized for the 128x64 screen.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
import tkinter as tk

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence, ImageTk
except ImportError as exc:  # pragma: no cover - handled at runtime
    Image = ImageDraw = ImageFont = ImageOps = ImageSequence = ImageTk = None
    PIL_IMPORT_ERROR = exc
else:
    PIL_IMPORT_ERROR = None


APP_NAME = "Flipper Asset Maker Pro"
APP_TITLE = f"{APP_NAME} | Roger F5 Tools"
SCREEN_WIDTH = 128
SCREEN_HEIGHT = 64
PREVIEW_SCALE = 4
MAX_NAME_LEN = 31
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
FRAME_RE = re.compile(r"^frame_(\d+)\.bm$", re.IGNORECASE)

try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS if Image else None
    RESAMPLE_NEAREST = Image.Resampling.NEAREST if Image else None
    DITHER_FS = Image.Dither.FLOYDSTEINBERG if Image else None
except AttributeError:  # Pillow < 9 compatibility
    RESAMPLE_LANCZOS = Image.LANCZOS if Image else None
    RESAMPLE_NEAREST = Image.NEAREST if Image else None
    DITHER_FS = Image.FLOYDSTEINBERG if Image else None


EXPORT_MODES = {
    "Momentum SD (/asset_packs/<Pack>/Anims)": "momentum",
    "Momentum legacy lowercase (/asset_packs/<Pack>/anims)": "momentum_legacy",
    "Official SD (/dolphin)": "official_sd",
    "Firmware source (/assets/dolphin/external)": "firmware_source",
}


@dataclass
class AnimationSettings:
    mode: str
    pack_name: str
    animation_name: str
    fps: int = 8
    duration: int = 3600
    min_butthurt: int = 0
    max_butthurt: int = 18
    min_level: int = 1
    max_level: int = 30
    weight: int = 3
    export_zip: bool = True


@dataclass
class ExportResult:
    dest_root: Path
    anims_root: Path
    animation_dir: Path
    manifest_path: Path
    meta_path: Path
    frame_count: int
    zip_path: Path | None = None


@dataclass
class ValidationReport:
    checked_roots: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def merge(self, other: "ValidationReport") -> None:
        self.checked_roots.extend(other.checked_roots)
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.infos.extend(other.infos)

    def to_text(self) -> str:
        lines = [
            f"Estado: {'OK' if self.ok else 'ERROR'}",
            f"Raices revisadas: {len(self.checked_roots)}",
            f"Errores: {len(self.errors)} | Warnings: {len(self.warnings)} | Info: {len(self.infos)}",
        ]
        if self.checked_roots:
            lines.append("")
            lines.append("[Raices]")
            lines.extend(f"- {root}" for root in self.checked_roots)
        if self.errors:
            lines.append("")
            lines.append("[Errores]")
            lines.extend(f"- {item}" for item in self.errors)
        if self.warnings:
            lines.append("")
            lines.append("[Warnings]")
            lines.extend(f"- {item}" for item in self.warnings)
        if self.infos:
            lines.append("")
            lines.append("[Info]")
            lines.extend(f"- {item}" for item in self.infos)
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


def require_pillow() -> None:
    if PIL_IMPORT_ERROR:
        raise RuntimeError(
            "Pillow no esta instalado. Ejecuta: python -m pip install -r requirements.txt"
        ) from PIL_IMPORT_ERROR


def sanitize_name(value: str, fallback: str = "Animacion") -> str:
    cleaned = SAFE_NAME_RE.sub("_", value.strip().replace(" ", "_"))
    cleaned = cleaned.strip("._-")
    if not cleaned:
        cleaned = fallback
    return cleaned[:MAX_NAME_LEN]


def parse_int(value: str | int, field_name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} debe ser un numero entero.") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field_name} debe estar entre {minimum} y {maximum}.")
    return parsed


def resolve_anims_root(dest_root: Path, settings: AnimationSettings) -> Path:
    if settings.mode == "momentum":
        return dest_root / "asset_packs" / settings.pack_name / "Anims"
    if settings.mode == "momentum_legacy":
        return dest_root / "asset_packs" / settings.pack_name / "anims"
    if settings.mode == "official_sd":
        return dest_root / "dolphin"
    if settings.mode == "firmware_source":
        return dest_root / "assets" / "dolphin" / "external"
    raise ValueError(f"Modo de exportacion no soportado: {settings.mode}")


def expected_bm_size(width: int, height: int) -> int:
    return ((width + 7) // 8) * height


def image_to_bm_bytes(frame: "Image.Image", width: int = SCREEN_WIDTH, height: int = SCREEN_HEIGHT) -> bytes:
    require_pillow()
    mono = frame.convert("1")
    if mono.size != (width, height):
        mono = mono.resize((width, height), RESAMPLE_NEAREST)
    pixels = mono.load()
    payload = bytearray()
    for y in range(height):
        for x in range(0, width, 8):
            byte = 0
            for bit in range(8):
                px = x + bit
                if px < width and pixels[px, y] == 0:
                    byte |= 1 << bit
            payload.append(byte)
    return bytes(payload)


def bm_bytes_to_image(payload: bytes, width: int, height: int) -> "Image.Image":
    require_pillow()
    img = Image.new("1", (width, height), 255)
    pixels = img.load()
    stride = (width + 7) // 8
    for y in range(height):
        for byte_x in range(stride):
            byte = payload[y * stride + byte_x]
            for bit in range(8):
                x = byte_x * 8 + bit
                if x < width and byte & (1 << bit):
                    pixels[x, y] = 0
    return img


def build_meta(settings: AnimationSettings, frame_count: int) -> str:
    order = " ".join(str(i) for i in range(frame_count))
    return (
        "Filetype: Flipper Animation\n"
        "Version: 1\n\n"
        f"Width: {SCREEN_WIDTH}\n"
        f"Height: {SCREEN_HEIGHT}\n"
        f"Passive frames: {frame_count}\n"
        "Active frames: 0\n"
        f"Frames order: {order}\n"
        "Active cycles: 0\n"
        f"Frame rate: {settings.fps}\n"
        f"Duration: {settings.duration}\n"
        "Active cooldown: 0\n\n"
        "Bubble slots: 0\n"
    )


def build_manifest_entry(settings: AnimationSettings) -> str:
    return (
        f"Name: {settings.animation_name}\n"
        f"Min butthurt: {settings.min_butthurt}\n"
        f"Max butthurt: {settings.max_butthurt}\n"
        f"Min level: {settings.min_level}\n"
        f"Max level: {settings.max_level}\n"
        f"Weight: {settings.weight}\n"
    )


def manifest_header() -> str:
    return "Filetype: Flipper Animation Manifest\nVersion: 1\n\n"


def split_manifest_entries(text: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    current: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("Name:"):
            if current:
                entries.append((entry_name(current), "\n".join(current).strip() + "\n"))
            current = [line]
        elif current:
            current.append(line)
    if current:
        entries.append((entry_name(current), "\n".join(current).strip() + "\n"))
    return entries


def entry_name(lines: list[str]) -> str:
    for line in lines:
        if line.startswith("Name:"):
            return line.split(":", 1)[1].strip()
    return ""


def upsert_manifest_entry(manifest_path: Path, settings: AnimationSettings) -> None:
    new_entry = build_manifest_entry(settings)
    existing = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else ""
    entries = split_manifest_entries(existing)
    output_entries: list[str] = []
    replaced = False
    for name, block in entries:
        if name == settings.animation_name:
            if not replaced:
                output_entries.append(new_entry)
                replaced = True
        else:
            output_entries.append(block)
    if not replaced:
        output_entries.append(new_entry)

    manifest_text = manifest_header() + "\n".join(block.strip() for block in output_entries).strip() + "\n"
    manifest_path.write_text(manifest_text, encoding="utf-8", newline="\n")


def create_zip_from_folder(source_dir: Path, zip_path: Path) -> Path:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in source_dir.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(source_dir.parent).as_posix())
    return zip_path


def write_animation_pack(
    dest_root: Path,
    frames: list["Image.Image"],
    settings: AnimationSettings,
    clean_target: bool = True,
) -> ExportResult:
    require_pillow()
    if not frames:
        raise ValueError("No hay frames para exportar.")

    settings.pack_name = sanitize_name(settings.pack_name, "AssetPack")
    settings.animation_name = sanitize_name(settings.animation_name, "Animacion")
    anims_root = resolve_anims_root(dest_root, settings)
    animation_dir = anims_root / settings.animation_name
    animation_dir.mkdir(parents=True, exist_ok=True)

    if clean_target:
        for stale_frame in animation_dir.glob("frame_*.bm"):
            stale_frame.unlink()

    for index, frame in enumerate(frames):
        frame_path = animation_dir / f"frame_{index}.bm"
        frame_path.write_bytes(image_to_bm_bytes(frame))

    meta_path = animation_dir / "meta.txt"
    meta_path.write_text(build_meta(settings, len(frames)), encoding="utf-8", newline="\n")

    manifest_path = anims_root / "manifest.txt"
    upsert_manifest_entry(manifest_path, settings)

    zip_path = None
    if settings.export_zip:
        if settings.mode in {"momentum", "momentum_legacy"}:
            zip_source = anims_root.parent
        elif settings.mode == "official_sd":
            zip_source = anims_root
        else:
            zip_source = anims_root.parent
        zip_path = create_zip_from_folder(zip_source, zip_source.with_suffix(".zip"))

    return ExportResult(
        dest_root=dest_root,
        anims_root=anims_root,
        animation_dir=animation_dir,
        manifest_path=manifest_path,
        meta_path=meta_path,
        frame_count=len(frames),
        zip_path=zip_path,
    )


def parse_key_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def parse_int_list(value: str) -> list[int]:
    if not value.strip():
        return []
    return [int(part) for part in value.split()]


def validate_manifest_entry(
    root: Path,
    entry: dict[str, str],
    report: ValidationReport,
    entry_number: int,
) -> None:
    name = entry.get("Name", "")
    if not name:
        report.errors.append(f"{root}: entrada #{entry_number} sin Name.")
        return

    animation_dir = root / name
    exact_names = {child.name for child in root.iterdir() if child.is_dir()}
    if name not in exact_names:
        if animation_dir.exists():
            report.warnings.append(f"{root}: Name '{name}' existe con distinta capitalizacion.")
        else:
            report.errors.append(f"{root}: Name '{name}' no tiene carpeta de animacion.")

    required = ["Min butthurt", "Max butthurt", "Min level", "Max level", "Weight"]
    for field_name in required:
        if field_name not in entry:
            report.errors.append(f"{root}: '{name}' no define {field_name}.")

    ranges = [
        ("Min butthurt", "Max butthurt", 0, 18),
        ("Min level", "Max level", 0, 30),
    ]
    for min_key, max_key, lower, upper in ranges:
        if min_key in entry and max_key in entry:
            try:
                min_value = parse_int(entry[min_key], min_key, lower, upper)
                max_value = parse_int(entry[max_key], max_key, lower, upper)
            except ValueError as exc:
                report.errors.append(f"{root}/{name}: {exc}")
                continue
            if min_value > max_value:
                report.errors.append(f"{root}/{name}: {min_key} no puede ser mayor que {max_key}.")

    if "Weight" in entry:
        try:
            parse_int(entry["Weight"], "Weight", 0, 100)
        except ValueError as exc:
            report.errors.append(f"{root}/{name}: {exc}")


def validate_animation_dir(animation_dir: Path, report: ValidationReport) -> None:
    meta_path = animation_dir / "meta.txt"
    if not meta_path.exists():
        report.errors.append(f"{animation_dir}: falta meta.txt.")
        return

    try:
        meta = parse_key_values(meta_path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        report.errors.append(f"{meta_path}: no se pudo leer ({exc}).")
        return

    required = [
        "Filetype",
        "Version",
        "Width",
        "Height",
        "Passive frames",
        "Active frames",
        "Frames order",
        "Frame rate",
        "Duration",
        "Active cooldown",
        "Bubble slots",
    ]
    for field_name in required:
        if field_name not in meta:
            report.errors.append(f"{meta_path}: falta campo '{field_name}'.")

    if meta.get("Filetype") != "Flipper Animation":
        report.errors.append(f"{meta_path}: Filetype invalido.")
    if meta.get("Version") != "1":
        report.errors.append(f"{meta_path}: Version debe ser 1.")

    try:
        width = parse_int(meta.get("Width", ""), "Width", 1, SCREEN_WIDTH)
        height = parse_int(meta.get("Height", ""), "Height", 1, SCREEN_HEIGHT)
        passive = parse_int(meta.get("Passive frames", ""), "Passive frames", 1, 1000)
        active = parse_int(meta.get("Active frames", "0"), "Active frames", 0, 1000)
        fps = parse_int(meta.get("Frame rate", ""), "Frame rate", 1, 30)
        parse_int(meta.get("Duration", ""), "Duration", 1, 86400)
        parse_int(meta.get("Active cooldown", "0"), "Active cooldown", 0, 86400)
        parse_int(meta.get("Bubble slots", "0"), "Bubble slots", 0, 10)
    except ValueError as exc:
        report.errors.append(f"{meta_path}: {exc}")
        return

    if fps > 15:
        report.warnings.append(f"{meta_path}: Frame rate {fps} es alto para animaciones largas.")

    try:
        order = parse_int_list(meta.get("Frames order", ""))
    except ValueError:
        report.errors.append(f"{meta_path}: Frames order contiene valores no numericos.")
        return

    expected_order_items = passive + active
    if len(order) != expected_order_items:
        report.errors.append(
            f"{meta_path}: Frames order tiene {len(order)} items y Passive+Active requiere {expected_order_items}."
        )

    frame_files = {
        int(match.group(1)): file_path
        for file_path in animation_dir.glob("frame_*.bm")
        if (match := FRAME_RE.match(file_path.name))
    }
    if not frame_files:
        report.errors.append(f"{animation_dir}: no hay frame_*.bm.")
        return

    expected_size = expected_bm_size(width, height)
    referenced = set(order)
    for frame_index in referenced:
        frame_path = frame_files.get(frame_index)
        if not frame_path:
            report.errors.append(f"{animation_dir}: frame_{frame_index}.bm referenciado pero no existe.")
            continue
        actual_size = frame_path.stat().st_size
        if actual_size != expected_size:
            report.errors.append(
                f"{frame_path}: tamano {actual_size} bytes, esperado {expected_size} para {width}x{height}."
            )

    extra_frames = sorted(set(frame_files) - referenced)
    if extra_frames:
        preview = ", ".join(f"frame_{idx}.bm" for idx in extra_frames[:6])
        suffix = "..." if len(extra_frames) > 6 else ""
        report.warnings.append(f"{animation_dir}: frames no referenciados: {preview}{suffix}")


def validate_animation_root(root: Path) -> ValidationReport:
    report = ValidationReport(checked_roots=[str(root)])
    manifest_path = root / "manifest.txt"
    if not manifest_path.exists():
        report.errors.append(f"{root}: falta manifest.txt.")
        return report

    try:
        manifest_text = manifest_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report.errors.append(f"{manifest_path}: no se pudo leer ({exc}).")
        return report

    manifest_values = parse_key_values(manifest_text)
    if manifest_values.get("Filetype") != "Flipper Animation Manifest":
        report.errors.append(f"{manifest_path}: Filetype invalido.")
    if manifest_values.get("Version") != "1":
        report.errors.append(f"{manifest_path}: Version debe ser 1.")

    entries = split_manifest_entries(manifest_text)
    if not entries:
        report.errors.append(f"{manifest_path}: no contiene entradas Name.")
        return report

    seen: set[str] = set()
    for number, (name, block) in enumerate(entries, start=1):
        if name in seen:
            report.warnings.append(f"{manifest_path}: Name duplicado '{name}'.")
        seen.add(name)
        validate_manifest_entry(root, parse_key_values(block), report, number)

    for child in sorted(root.iterdir()):
        if child.is_dir():
            validate_animation_dir(child, report)

    if root.name == "anims":
        report.warnings.append(f"{root}: carpeta legacy 'anims'; Momentum documenta 'Anims'.")
    report.infos.append(f"{root}: {len(entries)} animacion(es) declarada(s).")
    return report


def discover_animation_roots(path: Path) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def add(candidate: Path) -> None:
        if (candidate / "manifest.txt").exists():
            key = str(candidate.resolve()).lower()
            if key not in seen:
                seen.add(key)
                roots.append(candidate)

    if path.is_file():
        return roots

    add(path)
    for manifest_path in path.rglob("manifest.txt"):
        add(manifest_path.parent)
    return roots


def validate_path(path: Path, include_zips: bool = True) -> ValidationReport:
    report = ValidationReport()
    if not path.exists():
        report.errors.append(f"{path}: no existe.")
        return report

    if path.is_file() and path.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory(prefix="flipper_asset_zip_") as tmp_dir:
            with zipfile.ZipFile(path) as archive:
                archive.extractall(tmp_dir)
            zip_report = validate_path(Path(tmp_dir), include_zips=False)
            zip_report.checked_roots = [f"{path}::{root}" for root in zip_report.checked_roots]
            report.merge(zip_report)
        return report

    if path.is_file():
        report.errors.append(f"{path}: se esperaba una carpeta o .zip.")
        return report

    roots = discover_animation_roots(path)
    if not roots:
        report.errors.append(f"{path}: no se encontraron manifest.txt de animaciones.")
    for root in roots:
        report.merge(validate_animation_root(root))

    if include_zips:
        for zip_candidate in sorted(path.glob("*.zip")):
            report.infos.append(f"Validando ZIP: {zip_candidate.name}")
            report.merge(validate_path(zip_candidate, include_zips=False))

    return report


def build_demo_frames() -> list["Image.Image"]:
    require_pillow()
    frames: list[Image.Image] = []
    for index in range(4):
        img = Image.new("1", (SCREEN_WIDTH, SCREEN_HEIGHT), 255)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, SCREEN_WIDTH - 1, SCREEN_HEIGHT - 1], outline=0)
        draw.text((8, 8), "RF5", fill=0)
        draw.rectangle([20 + index * 18, 36, 34 + index * 18, 50], fill=0)
        draw.line([0, 60 - index * 4, SCREEN_WIDTH, 60 - index * 4], fill=0)
        frames.append(img)
    return frames


def run_self_test() -> ValidationReport:
    settings = AnimationSettings(
        mode="momentum",
        pack_name="Self_Test_Pack",
        animation_name="Self_Test_Anim",
        fps=8,
        duration=120,
        min_butthurt=0,
        max_butthurt=18,
        min_level=1,
        max_level=30,
        weight=5,
        export_zip=True,
    )
    with tempfile.TemporaryDirectory(prefix="flipper_asset_selftest_") as tmp_dir:
        result = write_animation_pack(Path(tmp_dir), build_demo_frames(), settings)
        report = validate_animation_root(result.anims_root)
        if result.zip_path and result.zip_path.exists():
            report.infos.append(f"ZIP generado: {result.zip_path.name}")
        return report


class FlipperAssetSuite:
    def __init__(self, root: tk.Tk):
        require_pillow()
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1240x860")
        self.root.minsize(1040, 720)
        self.root.configure(bg="#171717")

        self.source_frames: list[Image.Image] = []
        self.source_path: Path | None = None
        self.current_frame_idx = 0
        self.drawing_layer = Image.new("RGBA", (SCREEN_WIDTH, SCREEN_HEIGHT), (0, 0, 0, 0))
        self.preview_tk = None
        self.last_export: ExportResult | None = None

        self.pack_root_name = tk.StringVar(value="RF5_AssetPack")
        self.project_name = tk.StringVar(value="Nueva_Animacion")
        self.export_mode_label = tk.StringVar(value="Momentum SD (/asset_packs/<Pack>/Anims)")
        self.fps = tk.StringVar(value="8")
        self.duration = tk.StringVar(value="3600")
        self.min_butthurt = tk.StringVar(value="0")
        self.max_butthurt = tk.StringVar(value="18")
        self.min_level = tk.StringVar(value="1")
        self.max_level = tk.StringVar(value="30")
        self.weight = tk.StringVar(value="3")
        self.max_frames = tk.StringVar(value="240")
        self.frame_step = tk.StringVar(value="1")
        self.export_zip = tk.BooleanVar(value=True)
        self.clean_target = tk.BooleanVar(value=True)
        self.open_after_export = tk.BooleanVar(value=True)

        self.img_x = tk.IntVar(value=0)
        self.img_y = tk.IntVar(value=0)
        self.img_scale = tk.DoubleVar(value=1.0)
        self.threshold = tk.IntVar(value=128)
        self.invert = tk.BooleanVar(value=False)
        self.dither = tk.BooleanVar(value=False)

        self.text_x = tk.IntVar(value=5)
        self.text_y = tk.IntVar(value=5)
        self.text_bold = tk.BooleanVar(value=False)
        self.text_white = tk.BooleanVar(value=False)
        self.text_line_spacing = tk.IntVar(value=2)
        self.font_size = tk.StringVar(value="8")

        self.brush_size = tk.IntVar(value=2)

        self.status_var = tk.StringVar(value="Listo. Carga una imagen/GIF o escribe texto para generar frames.")

        self.setup_ui()
        self.bind_events()
        self.update_preview()

    def setup_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background="#242424", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 7), background="#303030", foreground="#f2f2f2")
        style.map("TNotebook.Tab", background=[("selected", "#eb5e28")], foreground=[("selected", "#ffffff")])
        style.configure("TCombobox", fieldbackground="#2b2b2b", foreground="#ffffff")

        sidebar = tk.Frame(self.root, bg="#242424", width=420)
        sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(12, 8), pady=12)
        sidebar.pack_propagate(False)

        title = tk.Label(
            sidebar,
            text="FLIPPER ASSET MAKER PRO",
            fg="#ff8a3d",
            bg="#242424",
            font=("Segoe UI", 15, "bold"),
        )
        title.pack(pady=(12, 4))
        subtitle = tk.Label(
            sidebar,
            text="BM + meta.txt + manifest.txt con validacion",
            fg="#b7b7b7",
            bg="#242424",
            font=("Segoe UI", 9),
        )
        subtitle.pack(pady=(0, 10))

        actions = tk.Frame(sidebar, bg="#242424")
        actions.pack(fill=tk.X, padx=12, pady=(0, 8))
        self.action_button(actions, "Cargar media", self.load_media).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        self.action_button(actions, "Auto fit", self.reset_transform).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

        tabs = ttk.Notebook(sidebar)
        tabs.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        tab_project = self.tab_frame(tabs)
        tabs.add(tab_project, text="Proyecto")
        self.create_label_entry(tab_project, "Nombre del pack", self.pack_root_name)
        self.create_label_entry(tab_project, "Nombre de animacion", self.project_name)
        self.create_combo(tab_project, "Destino", self.export_mode_label, list(EXPORT_MODES))
        self.two_column_numbers(tab_project, "FPS", self.fps, "Duracion", self.duration, 1, 30, 1, 86400)
        self.two_column_numbers(tab_project, "Min butthurt", self.min_butthurt, "Max butthurt", self.max_butthurt, 0, 18, 0, 18)
        self.two_column_numbers(tab_project, "Min level", self.min_level, "Max level", self.max_level, 0, 30, 0, 30)
        self.create_number(tab_project, "Weight", self.weight, 0, 100)
        self.create_check(tab_project, "Generar ZIP instalable", self.export_zip)
        self.create_check(tab_project, "Limpiar frames viejos al reexportar", self.clean_target)
        self.create_check(tab_project, "Abrir carpeta al terminar", self.open_after_export)

        tab_media = self.tab_frame(tabs)
        tabs.add(tab_media, text="Media")
        self.create_slider(tab_media, "Escala", self.img_scale, 0.05, 5.0, 0.01)
        self.create_slider(tab_media, "Offset X", self.img_x, -128, 128, 1)
        self.create_slider(tab_media, "Offset Y", self.img_y, -64, 64, 1)
        self.create_slider(tab_media, "Umbral B/N", self.threshold, 0, 255, 1)
        self.two_column_numbers(tab_media, "Max frames", self.max_frames, "Saltar cada N", self.frame_step, 1, 1000, 1, 60)
        self.create_check(tab_media, "Invertir colores", self.invert, self.update_preview)
        self.create_check(tab_media, "Dither Floyd-Steinberg", self.dither, self.update_preview)
        self.frame_info = tk.Label(tab_media, text="Frames: 0", bg="#242424", fg="#a0a0a0")
        self.frame_info.pack(anchor="w", padx=10, pady=8)

        tab_text = self.tab_frame(tabs)
        tabs.add(tab_text, text="Texto")
        tk.Label(tab_text, text="Texto overlay", fg="#dddddd", bg="#242424").pack(anchor="w", padx=10, pady=(10, 2))
        self.text_box = tk.Text(
            tab_text,
            height=5,
            bg="#303030",
            fg="white",
            insertbackground="white",
            borderwidth=0,
            wrap=tk.WORD,
        )
        self.text_box.pack(fill=tk.X, padx=10, pady=4)
        self.text_box.bind("<KeyRelease>", lambda _event: self.update_preview())
        self.two_column_numbers(tab_text, "Font", self.font_size, "Line spacing", self.text_line_spacing, 6, 18, 0, 20)
        self.create_slider(tab_text, "Texto X", self.text_x, 0, SCREEN_WIDTH, 1)
        self.create_slider(tab_text, "Texto Y", self.text_y, 0, SCREEN_HEIGHT, 1)
        self.create_check(tab_text, "Simular bold", self.text_bold, self.update_preview)
        self.create_check(tab_text, "Texto blanco / borrar", self.text_white, self.update_preview)

        tab_draw = self.tab_frame(tabs)
        tabs.add(tab_draw, text="Dibujo")
        tk.Label(tab_draw, text="Click izquierdo pinta, derecho borra.", fg="#d0d0d0", bg="#242424").pack(anchor="w", padx=10, pady=10)
        self.create_slider(tab_draw, "Tamano brocha", self.brush_size, 1, 8, 1)
        self.action_button(tab_draw, "Limpiar dibujo", self.clear_drawing, "#7a1d1d").pack(fill=tk.X, padx=10, pady=8)
        self.action_button(tab_draw, "Centrar media", self.center_media).pack(fill=tk.X, padx=10, pady=4)

        tab_qa = self.tab_frame(tabs)
        tabs.add(tab_qa, text="QA")
        tk.Label(tab_qa, text="Validacion de packs existentes", fg="#d0d0d0", bg="#242424").pack(anchor="w", padx=10, pady=(10, 4))
        self.action_button(tab_qa, "Validar carpeta o pack", self.validate_selected_path).pack(fill=tk.X, padx=10, pady=4)
        self.action_button(tab_qa, "Validar asset_packs local", self.validate_local_examples).pack(fill=tk.X, padx=10, pady=4)
        self.action_button(tab_qa, "Ejecutar self-test", self.run_gui_self_test).pack(fill=tk.X, padx=10, pady=4)

        self.action_button(
            sidebar,
            "GENERAR PACK VALIDADO",
            self.export_pack,
            "#eb5e28",
            ("Segoe UI", 11, "bold"),
        ).pack(fill=tk.X, padx=16, pady=(10, 12), ipady=7)

        tk.Label(sidebar, text="By Roger F5 Tools", fg="#777777", bg="#242424", font=("Segoe UI", 9)).pack(side=tk.BOTTOM, pady=8)

        main_area = tk.Frame(self.root, bg="#171717")
        main_area.pack(side=tk.RIGHT, expand=True, fill=tk.BOTH, padx=(0, 12), pady=12)

        preview_header = tk.Frame(main_area, bg="#171717")
        preview_header.pack(fill=tk.X, pady=(6, 8))
        tk.Label(
            preview_header,
            text="Preview 128x64",
            fg="#f5f5f5",
            bg="#171717",
            font=("Segoe UI", 15, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(preview_header, textvariable=self.status_var, fg="#a8a8a8", bg="#171717").pack(side=tk.RIGHT)

        self.canvas_container = tk.Frame(
            main_area,
            bg="#ff8200",
            width=SCREEN_WIDTH * PREVIEW_SCALE,
            height=SCREEN_HEIGHT * PREVIEW_SCALE,
            cursor="crosshair",
            highlightbackground="#3a3a3a",
            highlightthickness=1,
        )
        self.canvas_container.pack(pady=(12, 8))
        self.canvas_container.pack_propagate(False)

        self.preview_label = tk.Label(self.canvas_container, bg="#ff8200")
        self.preview_label.pack(expand=True)
        self.preview_label.bind("<B1-Motion>", lambda event: self.paint(event, "black"))
        self.preview_label.bind("<Button-1>", lambda event: self.paint(event, "black"))
        self.preview_label.bind("<B3-Motion>", lambda event: self.paint(event, "eraser"))
        self.preview_label.bind("<Button-3>", lambda event: self.paint(event, "eraser"))

        timeline_row = tk.Frame(main_area, bg="#171717")
        timeline_row.pack(fill=tk.X, padx=22, pady=(6, 2))
        self.action_button(timeline_row, "<", lambda: self.step_frame(-1)).pack(side=tk.LEFT, padx=(0, 6))
        self.timeline = tk.Scale(
            timeline_row,
            from_=0,
            to=0,
            orient=tk.HORIZONTAL,
            bg="#171717",
            fg="#ffffff",
            troughcolor="#2f2f2f",
            highlightthickness=0,
            command=self.change_frame,
        )
        self.timeline.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.action_button(timeline_row, ">", lambda: self.step_frame(1)).pack(side=tk.LEFT, padx=(6, 0))

        tk.Label(main_area, text="Reporte / validacion", fg="#f5f5f5", bg="#171717", font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=22, pady=(12, 3)
        )
        self.report_box = scrolledtext.ScrolledText(
            main_area,
            height=13,
            bg="#101010",
            fg="#dcdcdc",
            insertbackground="white",
            borderwidth=0,
            font=("Consolas", 9),
        )
        self.report_box.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 8))
        self.write_report("Listo. El validador revisa manifest.txt, meta.txt, frames .bm, tamanos y referencias.")

    def tab_frame(self, tabs: ttk.Notebook) -> tk.Frame:
        return tk.Frame(tabs, bg="#242424")

    def action_button(self, parent: tk.Widget, text: str, command, bg: str = "#3d3d3d", font=None) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg="white",
            activebackground="#ff8a3d",
            activeforeground="white",
            relief=tk.FLAT,
            cursor="hand2",
            font=font or ("Segoe UI", 9, "bold"),
        )

    def create_label_entry(self, parent: tk.Widget, label: str, variable: tk.StringVar) -> None:
        tk.Label(parent, text=label, fg="#d0d0d0", bg="#242424").pack(anchor="w", padx=10, pady=(10, 2))
        tk.Entry(
            parent,
            textvariable=variable,
            bg="#303030",
            fg="white",
            insertbackground="white",
            relief=tk.FLAT,
        ).pack(fill=tk.X, padx=10, pady=2, ipady=4)

    def create_combo(self, parent: tk.Widget, label: str, variable: tk.StringVar, values: list[str]) -> None:
        tk.Label(parent, text=label, fg="#d0d0d0", bg="#242424").pack(anchor="w", padx=10, pady=(10, 2))
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combo.pack(fill=tk.X, padx=10, pady=2)

    def create_number(self, parent: tk.Widget, label: str, variable, minimum: int, maximum: int) -> None:
        tk.Label(parent, text=label, fg="#d0d0d0", bg="#242424").pack(anchor="w", padx=10, pady=(8, 2))
        tk.Spinbox(
            parent,
            from_=minimum,
            to=maximum,
            textvariable=variable,
            bg="#303030",
            fg="white",
            insertbackground="white",
            buttonbackground="#3d3d3d",
            relief=tk.FLAT,
            command=self.update_preview,
        ).pack(fill=tk.X, padx=10, pady=2, ipady=2)

    def two_column_numbers(
        self,
        parent: tk.Widget,
        left_label: str,
        left_var,
        right_label: str,
        right_var,
        left_min: int,
        left_max: int,
        right_min: int,
        right_max: int,
    ) -> None:
        row = tk.Frame(parent, bg="#242424")
        row.pack(fill=tk.X, padx=10, pady=(8, 2))
        left = tk.Frame(row, bg="#242424")
        right = tk.Frame(row, bg="#242424")
        left.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        right.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))
        tk.Label(left, text=left_label, fg="#d0d0d0", bg="#242424").pack(anchor="w")
        tk.Label(right, text=right_label, fg="#d0d0d0", bg="#242424").pack(anchor="w")
        for frame, variable, minimum, maximum in (
            (left, left_var, left_min, left_max),
            (right, right_var, right_min, right_max),
        ):
            tk.Spinbox(
                frame,
                from_=minimum,
                to=maximum,
                textvariable=variable,
                bg="#303030",
                fg="white",
                insertbackground="white",
                buttonbackground="#3d3d3d",
                relief=tk.FLAT,
                command=self.update_preview,
            ).pack(fill=tk.X, pady=2, ipady=2)

    def create_slider(self, parent: tk.Widget, label: str, variable, start: float, end: float, step: float) -> None:
        tk.Label(parent, text=label, fg="#d0d0d0", bg="#242424").pack(anchor="w", padx=10, pady=(7, 0))
        tk.Scale(
            parent,
            from_=start,
            to=end,
            variable=variable,
            resolution=step,
            orient=tk.HORIZONTAL,
            bg="#242424",
            fg="white",
            troughcolor="#303030",
            highlightthickness=0,
            command=lambda _value: self.update_preview(),
        ).pack(fill=tk.X, padx=10)

    def create_check(self, parent: tk.Widget, label: str, variable: tk.BooleanVar, command=None) -> None:
        tk.Checkbutton(
            parent,
            text=label,
            variable=variable,
            bg="#242424",
            fg="white",
            activebackground="#242424",
            activeforeground="white",
            selectcolor="#101010",
            command=command,
        ).pack(anchor="w", padx=10, pady=3)

    def bind_events(self) -> None:
        self.root.bind("<Control-plus>", lambda _event: self.adjust_zoom(0.05))
        self.root.bind("<Control-minus>", lambda _event: self.adjust_zoom(-0.05))
        self.root.bind("<Control-0>", lambda _event: self.reset_transform())
        self.root.bind("<Left>", lambda _event: self.step_frame(-1))
        self.root.bind("<Right>", lambda _event: self.step_frame(1))

    def adjust_zoom(self, delta: float) -> None:
        self.img_scale.set(round(max(0.05, min(5.0, self.img_scale.get() + delta)), 2))
        self.update_preview()

    def center_media(self) -> None:
        self.img_x.set(0)
        self.img_y.set(0)
        self.update_preview()

    def reset_transform(self) -> None:
        self.center_media()
        if self.source_frames:
            width, height = self.source_frames[0].size
            self.img_scale.set(round(min(SCREEN_WIDTH / width, SCREEN_HEIGHT / height), 3))
        self.update_preview()

    def clear_drawing(self) -> None:
        self.drawing_layer = Image.new("RGBA", (SCREEN_WIDTH, SCREEN_HEIGHT), (0, 0, 0, 0))
        self.update_preview()

    def paint(self, event: tk.Event, tool: str) -> None:
        x = int(event.x / PREVIEW_SCALE)
        y = int(event.y / PREVIEW_SCALE)
        if x < 0 or y < 0 or x >= SCREEN_WIDTH or y >= SCREEN_HEIGHT:
            return
        radius = max(1, int(self.brush_size.get()))
        draw = ImageDraw.Draw(self.drawing_layer)
        fill = (0, 0, 0, 255) if tool == "black" else (0, 0, 0, 0)
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=fill)
        self.update_preview()

    def load_media(self) -> None:
        filetypes = [
            ("Media compatible", "*.gif *.png *.jpg *.jpeg *.bmp *.webp"),
            ("Todos los archivos", "*.*"),
        ]
        path = filedialog.askopenfilename(filetypes=filetypes)
        if not path:
            return

        try:
            image = Image.open(path)
            frames: list[Image.Image] = []
            for frame in ImageSequence.Iterator(image):
                frames.append(ImageOps.exif_transpose(frame.copy()).convert("RGBA"))
            if not frames:
                raise ValueError("El archivo no contiene frames renderizables.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"No se pudo cargar la media:\n{exc}")
            return

        self.source_frames = frames
        self.source_path = Path(path)
        self.current_frame_idx = 0
        self.timeline.configure(to=max(0, len(frames) - 1))
        self.timeline.set(0)
        self.frame_info.config(text=f"Frames cargados: {len(frames)} | {self.source_path.name}")
        self.reset_transform()
        self.status_var.set(f"Media cargada: {self.source_path.name}")

    def change_frame(self, value: str) -> None:
        self.current_frame_idx = int(float(value))
        self.update_preview()

    def step_frame(self, delta: int) -> None:
        if not self.source_frames:
            return
        next_index = max(0, min(len(self.source_frames) - 1, self.current_frame_idx + delta))
        self.timeline.set(next_index)
        self.current_frame_idx = next_index
        self.update_preview()

    def collect_settings(self) -> AnimationSettings:
        mode_label = self.export_mode_label.get()
        mode = EXPORT_MODES.get(mode_label)
        if not mode:
            raise ValueError("Selecciona un modo de exportacion valido.")
        pack_name = sanitize_name(self.pack_root_name.get(), "AssetPack")
        anim_name = sanitize_name(self.project_name.get(), "Animacion")
        return AnimationSettings(
            mode=mode,
            pack_name=pack_name,
            animation_name=anim_name,
            fps=parse_int(self.fps.get(), "FPS", 1, 30),
            duration=parse_int(self.duration.get(), "Duracion", 1, 86400),
            min_butthurt=parse_int(self.min_butthurt.get(), "Min butthurt", 0, 18),
            max_butthurt=parse_int(self.max_butthurt.get(), "Max butthurt", 0, 18),
            min_level=parse_int(self.min_level.get(), "Min level", 0, 30),
            max_level=parse_int(self.max_level.get(), "Max level", 0, 30),
            weight=parse_int(self.weight.get(), "Weight", 0, 100),
            export_zip=self.export_zip.get(),
        )

    def frame_indices_for_export(self) -> list[int]:
        if not self.source_frames:
            return [0]
        step = parse_int(self.frame_step.get(), "Saltar cada N", 1, 60)
        max_frames = parse_int(self.max_frames.get(), "Max frames", 1, 1000)
        return list(range(0, len(self.source_frames), step))[:max_frames]

    def has_content(self) -> bool:
        text_content = self.text_box.get("1.0", tk.END).strip()
        drawing_has_alpha = bool(self.drawing_layer.getbbox())
        return bool(self.source_frames or text_content or drawing_has_alpha)

    def compose_frame(self, frame_idx: int) -> "Image.Image":
        base = Image.new("L", (SCREEN_WIDTH, SCREEN_HEIGHT), 255)

        if self.source_frames:
            frame_idx = max(0, min(frame_idx, len(self.source_frames) - 1))
            current_img = self.source_frames[frame_idx]
            width, height = current_img.size
            scale = max(0.05, self.img_scale.get())
            new_width = max(1, int(width * scale))
            new_height = max(1, int(height * scale))
            resized = current_img.resize((new_width, new_height), RESAMPLE_LANCZOS)
            temp = Image.new("RGBA", (SCREEN_WIDTH, SCREEN_HEIGHT), (255, 255, 255, 0))
            origin = (
                (SCREEN_WIDTH - new_width) // 2 + self.img_x.get(),
                (SCREEN_HEIGHT - new_height) // 2 + self.img_y.get(),
            )
            temp.paste(resized, origin, resized)
            alpha = temp.getchannel("A")
            base.paste(ImageOps.grayscale(temp), (0, 0), alpha)

        if self.drawing_layer.getbbox():
            base.paste(self.drawing_layer.convert("L"), (0, 0), self.drawing_layer)

        text_content = self.text_box.get("1.0", tk.END).strip()
        if text_content:
            draw = ImageDraw.Draw(base)
            font = self.load_font(parse_int(self.font_size.get(), "Font", 6, 18))
            coords = (self.text_x.get(), self.text_y.get())
            fill = 255 if self.text_white.get() else 0
            if self.text_bold.get():
                draw.multiline_text(
                    (coords[0] + 1, coords[1]),
                    text_content,
                    fill=fill,
                    font=font,
                    spacing=self.text_line_spacing.get(),
                )
            draw.multiline_text(coords, text_content, fill=fill, font=font, spacing=self.text_line_spacing.get())

        if self.invert.get():
            base = ImageOps.invert(base)

        if self.dither.get():
            return base.convert("1", dither=DITHER_FS)

        threshold = self.threshold.get()
        return base.point(lambda pixel: 0 if pixel < threshold else 255, "1")

    def load_font(self, size: int):
        for font_name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(font_name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def update_preview(self) -> None:
        try:
            frame = self.compose_frame(self.current_frame_idx)
            visual = Image.new("RGBA", (SCREEN_WIDTH, SCREEN_HEIGHT), (255, 130, 0, 255))
            pixels = frame.load()
            overlay = visual.load()
            for y in range(SCREEN_HEIGHT):
                for x in range(SCREEN_WIDTH):
                    if pixels[x, y] == 0:
                        overlay[x, y] = (0, 0, 0, 255)
            visual = visual.resize((SCREEN_WIDTH * PREVIEW_SCALE, SCREEN_HEIGHT * PREVIEW_SCALE), RESAMPLE_NEAREST)
            self.preview_tk = ImageTk.PhotoImage(visual)
            self.preview_label.configure(image=self.preview_tk)
        except Exception as exc:
            self.status_var.set(f"Preview error: {exc}")

    def export_pack(self) -> None:
        if not self.has_content():
            messagebox.showerror(APP_NAME, "No hay contenido para exportar.")
            return

        try:
            settings = self.collect_settings()
            indices = self.frame_indices_for_export()
            frames = [self.compose_frame(index) for index in indices]
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return

        dest = filedialog.askdirectory(title="Seleccionar carpeta base de exportacion")
        if not dest:
            return

        try:
            self.last_export = write_animation_pack(Path(dest), frames, settings, clean_target=self.clean_target.get())
            report = validate_animation_root(self.last_export.anims_root)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Error exportando:\n{exc}")
            return

        self.write_report(report.to_text())
        self.status_var.set(f"Exportado: {self.last_export.animation_dir}")

        if report.ok:
            zip_info = f"\nZIP: {self.last_export.zip_path}" if self.last_export.zip_path else ""
            messagebox.showinfo(
                APP_NAME,
                f"Pack generado y validado.\nFrames: {self.last_export.frame_count}\nDestino: {self.last_export.animation_dir}{zip_info}",
            )
        else:
            messagebox.showwarning(APP_NAME, "El pack se genero, pero el validador encontro errores. Revisa el reporte.")

        if self.open_after_export.get() and os.name == "nt":
            os.startfile(str(self.last_export.dest_root))

    def validate_selected_path(self) -> None:
        path = filedialog.askdirectory(title="Seleccionar carpeta a validar")
        if not path:
            zip_path = filedialog.askopenfilename(title="O selecciona un ZIP", filetypes=[("ZIP", "*.zip")])
            path = zip_path
        if not path:
            return
        report = validate_path(Path(path))
        self.write_report(report.to_text())
        self.status_var.set("Validacion OK" if report.ok else "Validacion con errores")

    def validate_local_examples(self) -> None:
        local = Path(__file__).resolve().parent / "asset_packs"
        report = validate_path(local)
        self.write_report(report.to_text())
        self.status_var.set("asset_packs local OK" if report.ok else "asset_packs local con errores")

    def run_gui_self_test(self) -> None:
        report = run_self_test()
        self.write_report(report.to_text())
        self.status_var.set("Self-test OK" if report.ok else "Self-test con errores")

    def write_report(self, text: str) -> None:
        self.report_box.configure(state=tk.NORMAL)
        self.report_box.delete("1.0", tk.END)
        self.report_box.insert(tk.END, text)
        self.report_box.configure(state=tk.NORMAL)


def cli_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Crea y valida packs de animaciones para Flipper Zero.")
    parser.add_argument("--validate", metavar="PATH", help="Valida una carpeta o ZIP de animaciones.")
    parser.add_argument("--self-test", action="store_true", help="Genera un pack temporal y valida su estructura.")
    parser.add_argument("--json", action="store_true", help="Salida JSON para --validate o --self-test.")
    parser.add_argument("--no-gui", action="store_true", help="No abre GUI; util para comprobar dependencias.")
    args = parser.parse_args(argv)

    try:
        require_pillow()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.self_test:
        report = run_self_test()
        print(report.to_json() if args.json else report.to_text())
        return 0 if report.ok else 1

    if args.validate:
        report = validate_path(Path(args.validate))
        print(report.to_json() if args.json else report.to_text())
        return 0 if report.ok else 1

    if args.no_gui:
        print("Dependencias OK. GUI no iniciada por --no-gui.")
        return 0

    root = tk.Tk()
    FlipperAssetSuite(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main(sys.argv[1:]))
