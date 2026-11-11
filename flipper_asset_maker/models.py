"""Validated configuration shared by the GUI and export services."""

from dataclasses import dataclass
import math
import re


def safe_name(name: str) -> str:
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,30}", name):
        raise ValueError("El nombre debe tener 1–31 letras ASCII, números, guiones o guiones bajos.")
    return name


@dataclass(frozen=True)
class Stroke:
    tool: str
    points: tuple[tuple[int, int], ...]
    width: int = 2


@dataclass(frozen=True)
class ImageSettings:
    fit: str = "contain"
    dither: bool = True
    invert: bool = False
    threshold: int = 128
    contrast: float = 1.0
    zoom: float = 1.0
    offset_x: int = 0
    offset_y: int = 0
    text: str = ""
    text_x: int = 5
    text_y: int = 5
    font_size: int = 10
    font: str = "Segoe UI"
    bold: bool = False
    text_white: bool = False
    text_spacing: int = 2
    text_center: bool = False
    strokes: tuple[Stroke, ...] = ()

    def __post_init__(self):
        if self.fit not in {"contain", "cover", "stretch"}:
            raise ValueError("Ajuste de imagen desconocido.")
        if not 0 <= self.threshold <= 255 or not 0.1 <= self.contrast <= 4:
            raise ValueError("Umbral o contraste fuera de rango.")
        if not math.isfinite(self.zoom) or not 0.05 <= self.zoom <= 8:
            raise ValueError("El tamaño debe estar entre 5% y 800%.")
        if abs(self.offset_x) > 512 or abs(self.offset_y) > 512:
            raise ValueError("Posición de imagen fuera de rango (±512).")
        if not 6 <= self.font_size <= 48 or not 0 <= self.text_spacing <= 20:
            raise ValueError("Fuente: 6–48 píxeles; interlineado: 0–20.")
        if len(self.text) > 2000:
            raise ValueError("Máximo 2000 caracteres por capa de texto.")
        if self.font not in {"Segoe UI", "Consolas", "Arial", "Pixel"}:
            raise ValueError("Fuente desconocida.")
        if abs(self.text_x) > 512 or abs(self.text_y) > 512:
            raise ValueError("Posición de texto fuera de rango.")
        if any(s.tool not in {"black", "white", "eraser"} or not 1 <= s.width <= 16 for s in self.strokes):
            raise ValueError("Trazo inválido.")


@dataclass(frozen=True)
class AnimationSettings:
    name: str = "MyAnimation"
    pack: str = "RogerF5"
    fps: int = 8
    duration: int = 60
    playback: str = "loop"
    order: tuple[int, ...] = ()
    target: str = "momentum"
    min_butthurt: int = 0
    max_butthurt: int = 14
    min_level: int = 1
    max_level: int = 3
    weight: int = 3

    def sequence(self, count: int) -> tuple[int, ...]:
        safe_name(self.name)
        safe_name(self.pack)
        if not 1 <= count <= 256:
            raise ValueError("Se requieren entre 1 y 256 fotogramas.")
        if not 1 <= self.fps <= 30 or not 1 <= self.duration <= 86400:
            raise ValueError("FPS: 1–30. Duración: 1–86400 segundos.")
        if self.playback not in {"loop", "pingpong"}:
            raise ValueError("Reproducción desconocida.")
        if self.target not in {"momentum", "momentum_legacy", "official", "firmware_source"}:
            raise ValueError("Destino desconocido.")
        if not 0 <= self.min_butthurt <= self.max_butthurt <= 18:
            raise ValueError("Butthurt: 0 ≤ mínimo ≤ máximo ≤ 18.")
        if not 0 <= self.min_level <= self.max_level <= 30 or not 0 <= self.weight <= 100:
            raise ValueError("Niveles: 0–30. Peso de selección: 0–100.")
        sequence = self.order or tuple(range(count))
        if len(sequence) > 512 or any(i < 0 or i >= count for i in sequence):
            raise ValueError("Orden inválido: usa índices existentes (0 a N−1), máximo 512 entradas.")
        if self.playback == "pingpong":
            sequence = sequence + sequence[-2:0:-1]
        if len(sequence) > 512:
            raise ValueError("La secuencia final supera 512 entradas.")
        return sequence
