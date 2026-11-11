"""Lightweight entry point; importing the CLI does not construct Tk windows."""

import argparse
import json
from pathlib import Path
import sys
import tempfile


def main(argv=None):
    parser = argparse.ArgumentParser(description="Flipper Asset Maker Pro | RogerF5")
    parser.add_argument("--no-gui", action="store_true", help="Comprobar dependencias sin abrir ventanas")
    parser.add_argument("--self-test", action="store_true", help="Conversión y exportación de prueba en carpeta temporal")
    parser.add_argument("--validate", metavar="PATH", help="Validar carpeta o ZIP de assets")
    parser.add_argument("--json", action="store_true", help="Salida JSON de diagnóstico")
    args = parser.parse_args(argv)
    try:
        from PIL import Image
        import customtkinter
        import serial
        from .image_processor import ImageProcessor
        from .models import AnimationSettings, ImageSettings
        from .pack_exporter import PackExporter

        if args.validate:
            from .validation import validate_path
            result = validate_path(args.validate)
            print(json.dumps({"ok": result.ok, "animations": result.animations, "errors": result.errors, "warnings": result.warnings}, ensure_ascii=False, indent=2) if args.json else result.to_text())
            return 0 if result.ok else 1
        if args.self_test:
            source = Image.new("RGB", (256, 128), "white")
            source.putpixel((0, 0), (0, 0, 0))
            frame = ImageProcessor.process(source, ImageSettings())
            payload = ImageProcessor.to_bm(frame)
            decoded = ImageProcessor.from_bm(payload)
            if frame.tobytes() != decoded.tobytes():
                raise RuntimeError("Falló la comparación BM de ida y vuelta.")
            with tempfile.TemporaryDirectory() as folder:
                result = PackExporter.export(folder, [frame], AnimationSettings())
                if not result.archive.exists():
                    raise RuntimeError("No se generó el ZIP.")
            report = {"ok": True, "bm_bytes": len(payload), "size": [128, 64], "export": "OK", "usb": "no probado en hardware"}
        elif args.no_gui:
            report = {"ok": True, "dependencies": "Pillow, CustomTkinter, pyserial"}
        else:
            from .gui import AppGUI
            AppGUI().mainloop()
            return 0
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else "\n".join(f"{k}: {v}" for k, v in report.items()))
        return 0
    except ImportError as error:
        print(f"Falta una dependencia: {error}. Ejecuta: python -m pip install -r requirements.txt", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
