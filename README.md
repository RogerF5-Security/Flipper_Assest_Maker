# Flipper Asset Maker

Aplicación de escritorio creada por RogerF5 para preparar imágenes y animaciones
para Flipper Zero. La versión **2.0.0.dev4** es una base modular para pruebas
locales con CustomTkinter, Pillow y gestión USB CLI.

## Iniciar en Windows

Requiere Python 3.10 o posterior con Tkinter.

```powershell
python -m pip install -r requirements.txt
python .\main.py
```

También puedes abrir `Launch_Asset_Maker.cmd` con doble clic. Los accesos anteriores
`Flipper_Assest_Maker.py` y `Assest_Maker.pyw` abren la nueva aplicación.

## Módulos

| Módulo | Funciones de esta base |
| --- | --- |
| Creador de assets | Arrastrar, escalar, centrar, mover por píxeles; texto, pincel, borrador, deshacer/rehacer; BM/XBM/GIF y pack |
| Animaciones | Imágenes, GIF o vídeos; timeline, edición de capas, FPS, orden editable, bucle/ida y vuelta, GIF editado y pack + ZIP |
| Gestor USB | Detectar COM, verificar identidad, explorar SD, eliminar archivos/carpetas vacías y cargar carpetas nuevas con MD5 |

La preview usa zoom entero, fondo `#FF8C00` y píxeles negros. Conversión,
exportación, detección y transferencia corren en un worker; Tk consume sus eventos
sin bloquear la ventana.

## Primera prueba

1. En **Creador de assets**, importa una imagen y ajusta el encuadre.
2. Arrastra sobre la pantalla para mover la imagen. Usa flechas, Centrar, +/− o la rueda para ajustar. La vista previa se actualiza sola.
3. En **Animaciones**, importa un GIF o varias imágenes; ajusta FPS y orden.
4. Elige un nombre nuevo y pulsa **Exportar pack + ZIP**.
5. Para USB, cierra qFlipper y conecta el dispositivo.
6. En **Gestor USB**, pulsa **Detectar**, selecciona el COM y **Conectar**.
7. Explora `/ext`. Para Momentum usa **Cargar último pack por USB** desde Animaciones.
8. Activa después el pack en los ajustes del firmware; la carga no lo activa.

**Agregar imágenes / GIF / vídeo** permite selección múltiple con Ctrl/Shift en
ambos módulos y añade los nuevos frames al final de la secuencia existente.
La vista previa selecciona el primer frame recién agregado; la barra de tiempo
permite recorrer todas las imágenes. **Reemplazar** inicia otra selección de
fuentes, conservando los ajustes y capas. Cancelar el selector o fallar al abrir
un archivo conserva la secuencia anterior. El total máximo es 256 frames.

### Edición directa

- **Mover imagen**: arrastra en la pantalla; flechas = 1 píxel y Shift+flechas = 8 píxeles. Rueda o +/− cambian el tamaño de la imagen.
- **Mover texto**: arrastra la capa de texto; flechas mueven el texto y rueda o +/− cambian su fuente.
- Pestaña **Imagen**: tamaño 5–800%, posición, ajuste, contraste, umbral e inversión.
- Pestaña **Texto**: varias líneas, cuatro tipografías, tamaño 6–48 px, negrita, color blanco, interlineado y centrado.
- Pestaña **Dibujo**: lápiz negro, pincel blanco y borrador que recupera la imagen original. Botón derecho = borrador. Un círculo indica el tamaño real del pincel sobre la pantalla.
- **Nuevo lienzo vacío** permite crear un pack solo con texto o dibujo.
- Los ajustes y capas se aplican a todos los frames. Al exportar o reproducir se componen automáticamente; no hace falta pulsar Aplicar.
- La pestaña **Pack** recupera niveles, butthurt y peso de selección. Los botones laterales validan carpetas y ZIP.

Los campos numéricos admiten quedar vacíos mientras escribes, así como signos y
separadores decimales (punto o coma). La casilla mantiene un borrador independiente
del valor numérico usado por el slider. Al salir del campo, pulsar Enter o aplicar
la conversión, un borrador incompleto o fuera de rango recupera el último valor
válido. Escape también restaura ese valor.

### Atajos y edición del dibujo

| Atajo | Acción |
| --- | --- |
| Ctrl+Z | Deshacer el último trazo o la limpieza del dibujo |
| Ctrl+Y / Ctrl+Shift+Z | Rehacer |
| B / E / W | Lápiz negro / borrador / pincel blanco |
| V / T | Mover imagen / mover texto |
| [ / ] | Reducir / aumentar grosor de pincel |
| Esc | Cancelar un trazo sin terminar o restaurar la posición anterior al arrastre |
| Espacio | Reproducir / pausar |
| RePág / AvPág | Frame anterior / siguiente |
| Inicio / Fin | Primer / último frame |
| F1 o botón ? | Mostrar ayuda de atajos |

Los atajos de dibujo funcionan desde la pantalla y los controles del módulo
activo. Al escribir en la caja de texto, Ctrl+Z/Y deshacen/rehacen texto y no
consumen el historial de dibujo. Los campos numéricos conservan su teclado normal.
Ctrl+Z durante un trazo sin terminar cancela ese trazo antes de quitar otro.

Los botones de deshacer/rehacer también están junto a la vista previa y se
desactivan cuando no hay acciones disponibles. La pestaña Dibujo muestra el
número de trazos, tamaño de pincel y acciones de historial. Limpiar el dibujo se
puede deshacer; dibujar después de deshacer descarta la rama de rehacer.
Se conservan hasta 50 estados por módulo.

Las flechas < y > junto a la barra de tiempo recorren imágenes sin arrastrar el
slider. Elegir manualmente otro frame pausa la reproducción.

### Vídeo a GIF o animación Flipper

Se admite MP4, MOV, MKV, AVI, WEBM, M4V y MPEG mediante **FFmpeg** instalado en el
PATH. No se descarga ni instala ningún binario automáticamente. En la pestaña
Pack define primero FPS, máximo de frames, salto de muestreo, inicio y duración
del fragmento; después importa el vídeo. **Reimportar con estos límites** aplica
cambios de muestreo. El valor inicial importa hasta 120 frames de un fragmento
de 8 segundos; el límite superior es 256 frames y 120 segundos.

Los originales se conservan en memoria en color a un máximo de 512×256 para
editar sin volver a decodificar el vídeo. Las capas no alteran el archivo fuente.
**Exportar GIF editado** genera un GIF monocromo 128×64 en bucle con el orden y
FPS seleccionados. GIF representa los tiempos en centésimas de segundo, por lo
que el intervalo se redondea a 10 ms. Saltar frames y conservar los mismos FPS
de exportación acelera la secuencia.

Las cargas crean una subcarpeta nueva y rechazan destinos existentes. Cancelar
puede dejar una carpeta parcial en la SD. La barra llega al 100% después de
verificar los checksums de todos los archivos.

## Formatos

| Destino | Contenido del ZIP | Instalación |
| --- | --- | --- |
| Momentum | `asset_packs/<Pack>/Anims/manifest.txt`, `<Anim>/meta.txt`, `frame_N.bm` | Copia a SD o carga del último pack por USB |
| Momentum legacy | `asset_packs/<Pack>/anims/...` | Igual, con nombre de carpeta en minúsculas |
| Dolphin oficial | `dolphin/manifest.txt`, `<Anim>/meta.txt`, `frame_N.bm` | Manual; combinar con el manifest existente |
| Fuente firmware | `assets/dolphin/external/manifest.txt`, `<Anim>/meta.txt`, `frame_N.png` | Compilar con las herramientas del firmware |

BM sin compresión: cabecera `00` + raster LSB-first, **1025 bytes por frame
128×64**. La v1 generaba raster sin cabecera. Los packs existentes se conservan;
no se migran automáticamente. El XBM exportado utiliza la polaridad del compilador
de Flipper: bit 1 = negro.

`Frames order` admite índices repetidos. Ida y vuelta se codifica mediante el
orden nativo. Los GIF se convierten a FPS fijos elegidos por el usuario; las
duraciones variables no se conservan. Límite: 256 frames por proyecto.

## Pruebas

```powershell
python .\main.py --no-gui
python .\main.py --self-test --json
python .\main.py --validate .\asset_packs --json
python -m unittest discover -s tests -v
python -m tests.gui_smoke
python -m tests.gui_regressions
```

La suite comprueba el codec contra el raster XBM del algoritmo oficial, ZIP,
metadatos, GIF, cancelación y protocolo serie con dispositivo simulado. La prueba
GUI abre una ventana real, procesa imágenes y comprueba navegación y cierre;
no abre el COM. Hay 32 pruebas del motor/protocolo, incluida conversión de vídeo
real cuando FFmpeg está disponible, y una prueba GUI de edición y exportación.
Además hay quince regresiones de GUI para entradas numéricas, importación,
historial de dibujo, atajos reales de teclado, separación del historial de texto,
cancelación de gestos, navegación, cursor del pincel y cambio rápido de pestañas.
Las pruebas con hardware quedan pendientes de validación manual.

### USB y conexión

Se aceptan propiedades con guiones bajos o puntos (`hardware_model` /
`hardware.model`), espacios de alineación y colores ANSI. Se sincronizan los
prompts iniciales y se prueban `device_info`, `info device` y `!` si hace falta;
el modelo debe seguir confirmando Flipper Zero antes de usar storage.

Si Windows muestra **Acceso denegado a COM3**, cierra otras instancias de Asset
Maker, qFlipper y terminales serie, reconecta el USB y vuelve a pulsar Detectar y
Conectar. La aplicación muestra el fallo con instrucciones, sin imprimir un
traceback de Python para errores de conexión esperados. Un fallo de identidad
incluye la respuesta recibida para diagnosticar el firmware.

## Código

- `main.py`: entrada principal.
- `flipper_asset_maker/gui.py`: `AppGUI`.
- `flipper_asset_maker/image_processor.py`: `ImageProcessor`.
- `flipper_asset_maker/usb_manager.py`: `FlipperUSBManager`.
- `flipper_asset_maker/pack_exporter.py`: `PackExporter` y metadatos.
- `flipper_asset_maker/models.py`: configuración validada.
- `flipper_asset_maker/media_loader.py`: importación de vídeo/GIF y exportación GIF.
- `flipper_asset_maker/validation.py`: validación de carpetas y ZIP sin modificar contenido.
- `flipper_asset_maker/numeric_field.py`: entrada numérica con borrador separado del slider.
- `flipper_asset_maker/editor_tabs.py`: protege la pestaña activa de callbacks de selección anteriores.
- `tests/`: pruebas de contratos e integración GUI.
- `legacy/Flipper_Assest_Maker_v1.py`: copia intacta del programa anterior.
- `asset_packs/`: packs existentes, conservados sin cambios.

La versión nueva ya incorpora dibujo, texto y validación. Esta base no incluye
RPC protobuf, reemplazo de iconos/fonts del sistema ni proyectos persistentes.
Los packs se exportan como carpetas nuevas; no se sobrescriben o combinan packs
existentes automáticamente. Consulta
[arquitectura y límites](docs/ARCHITECTURE.md).

Referencias oficiales:
[codec](https://github.com/flipperdevices/flipperzero-firmware/blob/dev/scripts/flipper/assets/icon.py),
[animaciones](https://github.com/flipperdevices/flipperzero-firmware/blob/dev/assets/dolphin/ReadMe.md),
[CLI storage](https://github.com/flipperdevices/flipperzero-firmware/blob/dev/applications/services/storage/storage_cli.c).

Repositorio: [RogerF5-Security/Flipper_Assest_Maker](https://github.com/RogerF5-Security/Flipper_Assest_Maker).
Versión de desarrollo 2.0.0.dev4. Las pruebas automáticas y de GUI están disponibles
en `tests/`; la validación de transferencia USB en hardware sigue pendiente.
