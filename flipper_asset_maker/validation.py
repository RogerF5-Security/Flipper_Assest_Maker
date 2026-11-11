"""Read-only validation of pack directories or ZIPs, including legacy BM mistakes."""

from dataclasses import dataclass, field
import io
from pathlib import Path, PurePosixPath
import re
import zipfile
from PIL import Image


@dataclass
class ValidationReport:
    animations: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors

    def to_text(self):
        return (f"Estado: {'OK' if self.ok else 'ERROR'}\nAnimaciones: {self.animations}\n"
                f"Errores: {len(self.errors)} · Avisos: {len(self.warnings)}\n\n" +
                "\n".join([f"ERROR: {s}" for s in self.errors] + [f"AVISO: {s}" for s in self.warnings]))


def _values(data):
    return {key.strip(): value.strip() for line in data.decode("utf-8-sig").splitlines()
            if ":" in line and not line.lstrip().startswith("#") for key, value in [line.split(":", 1)]}


def validate_path(path, cancel=None):
    path = Path(path)
    report = ValidationReport()

    def validate(names, read):
        metas = sorted(name for name in names if PurePosixPath(name).name == "meta.txt")
        if not metas:
            report.errors.append("No se encontró ninguna animación con meta.txt.")
        for name in metas:
            if cancel and cancel.is_set():
                raise InterruptedError("Validación cancelada.")
            report.animations += 1
            root = PurePosixPath(name).parent
            try:
                data = _values(read(name))
                if data.get("Filetype") != "Flipper Animation" or data.get("Version") != "1":
                    raise ValueError("cabecera de meta.txt inválida")
                width, height = int(data["Width"]), int(data["Height"])
                if not 1 <= width <= 128 or not 1 <= height <= 64:
                    raise ValueError("dimensiones fuera de 128×64")
                fps, duration = int(data["Frame rate"]), int(data["Duration"])
                if not 1 <= fps <= 30 or not 1 <= duration <= 86400:
                    raise ValueError("FPS o duración fuera de rango")
                passive, active = int(data["Passive frames"]), int(data["Active frames"])
                order = tuple(map(int, data["Frames order"].split()))
                if passive < 1 or active < 0 or len(order) != passive + active or any(index < 0 for index in order):
                    raise ValueError("Frames order no coincide con Passive/Active frames")
                for key in ("Active cycles", "Active cooldown", "Bubble slots"):
                    if int(data[key]) < 0:
                        raise ValueError(f"{key} negativo")
                for index in set(order):
                    file = f"{root}/frame_{index}.bm"
                    png = f"{root}/frame_{index}.png"
                    if file not in names:
                        if png not in names:
                            raise ValueError(f"falta frame_{index}.bm/.png")
                        with Image.open(io.BytesIO(read(png))) as image:
                            if image.size != (width, height):
                                raise ValueError(f"frame_{index}.png: dimensiones incorrectas")
                        continue
                    payload = read(file)
                    expected = ((width + 7) // 8) * height
                    if len(payload) == expected:
                        raise ValueError(f"frame_{index}.bm: raster legado sin cabecera; reexportar desde la fuente")
                    if payload[:1] == b"\x00":
                        if len(payload) != expected + 1:
                            raise ValueError(f"frame_{index}.bm: tamaño incorrecto")
                    elif payload[:2] == b"\x01\x00" and len(payload) >= 4:
                        if int.from_bytes(payload[2:4], "little") != len(payload) - 4:
                            raise ValueError(f"frame_{index}.bm: cabecera comprimida inconsistente")
                        report.warnings.append(f"{file}: cabecera heatshrink válida; contenido no descomprimido.")
                    else:
                        raise ValueError(f"frame_{index}.bm: cabecera desconocida")
                manifest = str(root.parent / "manifest.txt")
                if manifest not in names:
                    raise ValueError("falta manifest.txt junto a la carpeta de animación")
                manifest_text = read(manifest).decode("utf-8-sig")
                header = _values(read(manifest))
                if header.get("Filetype") != "Flipper Animation Manifest" or header.get("Version") != "1":
                    raise ValueError("cabecera de manifest inválida")
                records = re.split(r"(?m)^Name:\s*", manifest_text)[1:]
                record = next((record for record in records if record.splitlines()[0].strip() == root.name), None)
                if record is None:
                    raise ValueError("manifest no referencia esta animación")
                entry = _values(("Name: " + record).encode())
                for lower, upper in (("Min butthurt", "Max butthurt"), ("Min level", "Max level")):
                    if not 0 <= int(entry[lower]) <= int(entry[upper]):
                        raise ValueError(f"rango {lower}/{upper} inválido")
                if int(entry["Weight"]) < 0:
                    raise ValueError("peso negativo")
            except (ValueError, KeyError, OSError, UnicodeError) as error:
                report.errors.append(f"{name}: {error}")

    if path.is_dir():
        files = {}
        for file in path.rglob("*"):
            if file.is_file():
                if file.is_symlink() or not file.resolve().is_relative_to(path.resolve()):
                    report.errors.append(f"No se siguen enlaces: {file.name}")
                    continue
                files[file.relative_to(path).as_posix()] = file
        # Also accept selecting a single animation folder.
        if (path / "meta.txt").exists():
            parent = path.parent
            files = {f"{path.name}/{name}": file for name, file in files.items()}
            if (parent / "manifest.txt").is_file():
                files["manifest.txt"] = parent / "manifest.txt"

        def read(name):
            if files[name].stat().st_size > 2_000_000:
                raise ValueError(f"Archivo demasiado grande: {name}")
            return files[name].read_bytes()
        validate(set(files), read)
    elif path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            items = archive.infolist()
            if len(items) > 10000 or sum(item.file_size for item in items) > 128_000_000:
                raise ValueError("ZIP demasiado grande para validación de assets.")
            if len({item.filename for item in items}) != len(items):
                report.errors.append("El ZIP contiene rutas duplicadas.")
            names = {item.filename for item in items if not item.is_dir()}
            for name in names:
                if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "\\" in name:
                    report.errors.append(f"Ruta insegura en ZIP: {name}")

            def read(name):
                if archive.getinfo(name).file_size > 2_000_000:
                    raise ValueError(f"Archivo demasiado grande: {name}")
                return archive.read(name)
            validate(names, read)
    else:
        raise ValueError("Selecciona una carpeta o un ZIP válido.")
    return report
