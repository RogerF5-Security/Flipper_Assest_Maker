# Flipper Asset Maker 

Herramienta GUI para crear paquetes de animaciones compatibles con Flipper Zero y Momentum Firmware. Convierte imagenes, GIFs y texto a frames `.bm` de 128x64, genera `meta.txt`, actualiza `manifest.txt` sin borrar otras animaciones y valida que la estructura exportada no tenga referencias rotas.

## Funciones principales

- GUI moderna en un solo archivo Python.
- Importa `PNG`, `JPG`, `BMP`, `GIF` y `WEBP`.
- Preview 128x64 con zoom, offset, threshold, inversion y dither.
- Overlay de texto, dibujo manual, borrador y control de brocha.
- Exporta para:
  - Momentum: `asset_packs/<Pack>/Anims`
  - Momentum legacy: `asset_packs/<Pack>/anims`
  - SD oficial: `dolphin`
  - fuente firmware: `assets/dolphin/external`
- Genera `.zip` instalable.
- Validador integrado de `manifest.txt`, `meta.txt` y `frame_*.bm`.
- CLI para automatizar QA: `--validate` y `--self-test`.

## Instalacion

```powershell
python -m pip install -r requirements.txt
```

## Uso GUI

```powershell
python .\Flipper_Assest_Maker.py
```

Flujo recomendado:

1. Carga una imagen o GIF.
2. Ajusta escala, offsets, threshold o dither.
3. Define pack, nombre de animacion, FPS, niveles y peso.
4. Exporta con `GENERAR PACK VALIDADO`.
5. Revisa el reporte. Si no hay errores, copia el ZIP o carpeta generada al Flipper.

## Uso CLI

Validar los ejemplos incluidos:

```powershell
python .\Flipper_Assest_Maker.py --validate .\asset_packs
```

Self-test de exportacion en carpeta temporal:

```powershell
python .\Flipper_Assest_Maker.py --self-test
```

Salida JSON para pipelines:

```powershell
python .\Flipper_Assest_Maker.py --validate .\asset_packs --json
```

## Criterios de validacion

El validador revisa:

- `manifest.txt` con `Filetype: Flipper Animation Manifest` y `Version: 1`.
- Cada `Name` del manifiesto debe coincidir con una carpeta de animacion.
- `meta.txt` con campos requeridos por el formato Flipper.
- `Width <= 128` y `Height <= 64`.
- `Frames order` debe coincidir con `Passive frames + Active frames`.
- Cada `frame_X.bm` referenciado debe existir.
- Tamano `.bm` esperado: `ceil(width / 8) * height`.
- Rango de butthurt, level, FPS, duration y weight.

## Estructura Momentum recomendada

```text
asset_packs/
  NombrePack/
    Anims/
      manifest.txt
      NombreAnimacion/
        meta.txt
        frame_0.bm
        frame_1.bm
```

Momentum carga los packs desde `SD/asset_packs`. La carpeta `Anims` es la forma documentada; la herramienta tambien puede exportar el modo legacy `anims` para packs antiguos.

## Notas de estabilidad para Flipper

- Mantener animaciones en 128x64 reduce riesgos de frames corruptos o tamanos incorrectos.
- FPS entre 1 y 15 es mas conservador para GIFs largos; la herramienta permite hasta 30 pero avisa cuando es alto.
- Usa `--validate` antes de copiar packs al dispositivo.
- Al reexportar con el mismo nombre, la opcion de limpiar frames viejos evita que queden `.bm` no referenciados.
