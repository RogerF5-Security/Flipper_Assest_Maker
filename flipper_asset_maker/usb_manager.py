"""Serialized Flipper CLI transport; callers run these blocking methods in a worker.

Binary uploads use storage write_chunk's Ready handshake, then storage md5.
No RPC session is opened and no commands are sent to unidentified hardware.
"""

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
import threading
import time

import serial
from serial.tools import list_ports


class FlipperUSBError(RuntimeError):
    pass


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str
    likely_flipper: bool


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    directory: bool
    size: int = 0


class FlipperUSBManager:
    # Consume the final space even when USB fragments the prompt after ':'.
    PROMPT = re.compile(rb"(?:^|\r?\n)>: ")
    READY = re.compile(rb"(?:^|\r?\n)Ready\r?\n|(?:^|\r?\n)>: ")
    ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

    def __init__(self, serial_factory=serial.Serial, timeout=6.0):
        self._factory = serial_factory
        self.timeout = timeout
        self._serial = None
        self._rx = bytearray()
        self._lock = threading.RLock()
        self.identity = ""
        self.port = ""
        self.device = {}
        self.diagnostic = ""

    @property
    def connected(self):
        return self._serial is not None and self._serial.is_open

    @staticmethod
    def detect_ports() -> list[PortInfo]:
        ports = []
        for port in list_ports.comports():
            text = f"{port.description} {port.manufacturer} {port.serial_number}".lower()
            likely = "flipper" in text or "flip_" in text or (port.vid, port.pid) == (0x0483, 0x5740)
            ports.append(PortInfo(port.device, port.description, likely))
        return sorted(ports, key=lambda p: (not p.likely_flipper, p.device))

    @staticmethod
    def path(value: str, writable=False) -> str:
        if any(ord(c) < 32 or c in {'"', '\\'} for c in value) or len(value) > 240:
            raise ValueError("Ruta remota inválida.")
        parsed = PurePosixPath(value)
        if not value.startswith("/") or ".." in parsed.parts:
            raise ValueError("Usa una ruta absoluta sin '..'.")
        if not parsed.parts or (len(parsed.parts) > 1 and parsed.parts[1] not in {"ext", "int"}):
            raise ValueError("La ruta debe comenzar con /ext o /int.")
        if writable and (len(parsed.parts) < 3 or parsed.parts[1] != "ext"):
            raise ValueError("Las escrituras requieren una subcarpeta de /ext.")
        return str(parsed)

    def _write_all(self, data: bytes):
        view = memoryview(data)
        while view:
            sent = self._serial.write(view)
            if not sent:
                raise FlipperUSBError("El puerto no acepta datos.")
            view = view[sent:]

    def _read_until(self, pattern):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            match = pattern.search(self._rx)
            if match:
                result = bytes(self._rx[:match.end()])
                del self._rx[:match.end()]
                return result
            if len(self._rx) > 2_000_000:
                raise FlipperUSBError("Respuesta CLI demasiado grande.")
            self._rx.extend(self._serial.read(max(1, min(self._serial.in_waiting, 8192))))
        raise FlipperUSBError("Tiempo de espera CLI agotado. Cierra qFlipper y reconecta el dispositivo.")

    @classmethod
    def _text(cls, response):
        return cls.ANSI.sub("", response.decode("utf-8", errors="replace"))

    @staticmethod
    def _check(text):
        if re.search(r"(?im)^\s*(Storage error:|Error:|Unknown command|Usage:|Invalid arguments)", text):
            raise FlipperUSBError(text.strip())

    @classmethod
    def parse_identity(cls, text):
        """Firmware can use underscores or dotted property names, with padded keys."""
        properties = {}
        for line in cls.ANSI.sub("", text).splitlines():
            match = re.match(r"\s*([A-Za-z][\w.]*)\s*:\s*(.*?)\s*$", line)
            if match:
                properties[match[1].replace(".", "_").lower()] = match[2]
        return properties

    @staticmethod
    def is_flipper(properties):
        return bool(re.fullmatch(r"Flipper(?:\s+Zero)?", properties.get("hardware_model", ""), re.I))

    def _settle_prompt(self):
        # Ctrl-C + Enter can generate TWO prompts. Drain the second before the
        # first command so it cannot be mistaken for an empty device_info reply.
        deadline = time.monotonic() + 0.18
        hard_deadline = time.monotonic() + 1.0
        while time.monotonic() < min(deadline, hard_deadline):
            if self._serial.in_waiting:
                self._serial.read(min(8192, self._serial.in_waiting))
                deadline = time.monotonic() + 0.08
            else:
                time.sleep(0.01)
        self._rx.clear()

    def _command(self, command):
        if not self.connected:
            raise FlipperUSBError("Conecta el Flipper primero.")
        try:
            self._write_all(command.encode("utf-8") + b"\r")
            text = self._text(self._read_until(self.PROMPT))
        except (serial.SerialException, OSError, FlipperUSBError):
            self.disconnect()
            raise
        self._check(text)
        return text

    def connect(self, port):
        with self._lock:
            self.disconnect()
            try:
                self._serial = self._factory(port=port, baudrate=115200, timeout=0.1, write_timeout=3)
                self.port = port
                self._write_all(b"\x03\r")
                self._read_until(self.PROMPT)
                self._settle_prompt()
                replies = []
                for command in ("device_info", "info device", "!"):
                    try:
                        identity = self._command(command)
                    except FlipperUSBError as error:
                        if not self.connected:
                            raise
                        replies.append(f"{command}: {error}")
                        continue
                    properties = self.parse_identity(identity)
                    replies.append(f"{command}: {identity}")
                    if self.is_flipper(properties):
                        self.identity = identity
                        self.device = properties
                        self.diagnostic = "\n".join(replies)
                        return identity
                    # A different model is a conclusive negative, no fallback needed.
                    if "hardware_model" in properties:
                        break
                self.diagnostic = "\n".join(replies)
                raise FlipperUSBError("No se pudo verificar el modelo Flipper Zero. Respuesta CLI:\n" + self.diagnostic[-1600:])
            except serial.SerialException as error:
                self.disconnect()
                raise FlipperUSBError(f"No se pudo abrir {port}: {error}\nCierra otras ventanas de Asset Maker, qFlipper y cualquier terminal que use el COM; después reconecta.") from error
            except Exception:
                self.disconnect()
                raise

    def disconnect(self):
        with self._lock:
            if self._serial:
                self._serial.close()
            self._serial = None
            self._rx.clear()
            self.port = ""
            self.identity = ""
            self.device = {}

    def list_directory(self, path="/ext"):
        with self._lock:
            response = self._command(f'storage list "{self.path(path)}"')
            entries = []
            for line in response.splitlines():
                match = re.match(r"\s*\[([DF])\] (.+?)(?: (\d+)b)?\s*$", line)
                if match:
                    entries.append(RemoteEntry(match[2], match[1] == "D", int(match[3] or 0)))
            return sorted(entries, key=lambda e: (not e.directory, e.name.lower()))

    def remove(self, path):
        with self._lock:
            self._command(f'storage remove "{self.path(path, writable=True)}"')

    def _stat(self, path):
        try:
            return self._command(f'storage stat "{self.path(path)}"')
        except FlipperUSBError as error:
            if re.search(r"Storage error:.*(?:not exist|not found)", str(error), re.I):
                return None
            raise

    def _mkdirs(self, path):
        path = PurePosixPath(self.path(path, writable=True))
        for index in range(3, len(path.parts) + 1):
            part = str(PurePosixPath(*path.parts[:index]))
            status = self._stat(part)
            if status is None:
                self._command(f'storage mkdir "{part}"')
            elif not re.search(r"(?m)^Directory\s*$", status):
                raise FlipperUSBError(f"La ruta ya existe y no es un directorio: {part}")

    def _upload_file(self, local, remote, tick, cancel):
        # Files are always under a newly-created root; append cannot corrupt old files.
        digest = hashlib.md5(usedforsecurity=False)
        with local.open("rb") as source:
            while True:
                if cancel and cancel.is_set():
                    raise InterruptedError("Carga cancelada; puede quedar una carpeta parcial en la SD.")
                block = source.read(1024)
                if not block:
                    break
                try:
                    self._write_all(f'storage write_chunk "{remote}" {len(block)}\r'.encode("utf-8"))
                    ready = self._text(self._read_until(self.READY))
                    self._check(ready)
                    if not re.search(r"(?m)^Ready\r?$", ready):
                        raise FlipperUSBError("El firmware no confirmó Ready.")
                    self._write_all(block)
                    self._check(self._text(self._read_until(self.PROMPT)))
                except Exception:
                    # A timeout after Ready leaves the protocol in an unknown binary state.
                    self.disconnect()
                    raise
                digest.update(block)
                tick(len(block), local.name)
        checksum = self._command(f'storage md5 "{remote}"')
        if digest.hexdigest() not in {s.lower() for s in re.findall(r"(?im)^([0-9a-f]{32})\r?$", checksum)}:
            raise FlipperUSBError(f"Checksum incorrecto: {remote}. La copia no se completó.")

    def upload_directory(self, local, parent="/ext/asset_packs", progress=None, cancel=None):
        with self._lock:
            local = Path(local).resolve()
            if not local.is_dir() or local.is_symlink():
                raise ValueError("Selecciona una carpeta local válida.")
            remote = self.path(f"{self.path(parent).rstrip('/')}/{local.name}", writable=True)
            files = sorted(p for p in local.rglob("*") if p.is_file())
            if not files:
                raise ValueError("La carpeta está vacía.")
            for file in files:
                if file.is_symlink() or not file.resolve().is_relative_to(local):
                    raise ValueError("No se permiten enlaces fuera del pack.")
                self.path(f"{remote}/{file.relative_to(local).as_posix()}", writable=True)
                if not file.stat().st_size:
                    raise ValueError(f"Archivo vacío no admitido: {file.name}")
            if self._stat(remote) is not None:
                raise FileExistsError(f"El destino ya existe: {remote}. Usa un nombre nuevo para conservarlo.")
            if cancel and cancel.is_set():
                raise InterruptedError("Carga cancelada.")
            self._mkdirs(remote)
            total = sum(file.stat().st_size for file in files)
            completed = 0

            def tick(amount, name):
                nonlocal completed
                completed += amount
                if progress:
                    # Reserve the final percentage until every checksum has passed.
                    progress(0.99 * completed / total, f"{name} · {completed:,}/{total:,} bytes")

            for file in files:
                target = f"{remote}/{file.relative_to(local).as_posix()}"
                self._mkdirs(str(PurePosixPath(target).parent))
                self._upload_file(file, target, tick, cancel)
            if progress:
                progress(1.0, "Carga completada y checksums verificados")
            return remote
