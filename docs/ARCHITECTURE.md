# Arquitectura v2 (base funcional)

- `AppGUI` (`gui.py`): ventana CustomTkinter, navegación, edición y preview.
- `ImageProcessor` (`image_processor.py`): Pillow, EXIF, transparencia, ajuste 128×64,
  contraste, Floyd–Steinberg/umbral, codec BM y exportación XBM.
- `PackExporter` (`pack_exporter.py`): construcción aislada de pack + ZIP + meta.
- `FlipperUSBManager` (`usb_manager.py`): detección, identificación, CLI y carga.
- `models.py`: configuración validada independiente de Tk.
- `cli.py`: diagnóstico y entrada de la aplicación.
- `MediaLoader` (`media_loader.py`): caché de originales, GIF e importación FFmpeg cancelable, exportación GIF.
- `validation.py`: lectura y validación de manifest/meta, BM sin compresión y PNG; revisión estructural de cabeceras comprimidas.
- `NumericField` (`numeric_field.py`): una `StringVar` para el borrador editable y una variable numérica para el último valor válido; el slider recibe números mediante `set`, sin enlazarlo al texto de la casilla.
- `EditorTabs` (`editor_tabs.py`): evita que el cierre diferido de una pestaña anterior oculte la pestaña seleccionada durante cambios rápidos en CTk 5.2.

## Concurrencia

Una tarea activa por aplicación evita comandos serie intercalados y conversiones
obsoletas. Cada tarea corre en un hilo daemon y entrega eventos a una cola.
Tk consume eventos mediante `after`; los workers no leen variables Tk ni tocan
widgets. La reproducción usa temporizadores Tk y previews PPM preparados por el
worker. El zoom entero se dibuja con Tk, sin interpolación de Pillow en el hilo UI.
Cambiar de módulo sigue disponible durante una tarea.

La edición usa además un único worker de preview con agrupación de cambios cada
80 ms. Cada resultado lleva revisión e índice de frame para descartar vistas
obsoletas. Los originales permanecen en memoria con un máximo de 512×256 por
frame; los cambios de escala, posición, texto y trazos no fuerzan reimportación.
El raster de dibujo se calcula en el worker a partir de trazos inmutables; el
hilo Tk solo captura coordenadas y dibuja la guía temporal del pincel. Hay hasta
50 estados de deshacer de la capa de dibujo.

Los atajos se registran en la ventana para que el dibujo no dependa de mantener
el foco en el canvas. Un filtro por tipo de widget respeta entradas, menús y
otras ventanas; la caja de texto enlaza su propio undo/redo. El historial por
módulo registra un estado por trazo o limpieza, no por movimiento del ratón.
Escape descarta el trazo provisional o restaura las coordenadas iniciales de un
arrastre. Las guías del pincel son elementos del canvas y no forman parte del
raster exportado. Los estados visuales de botones solo se actualizan si cambian,
para evitar redibujarlos en cada frame de reproducción.

La selección de fuentes es múltiple en ambos módulos. Agregar carga solo los
archivos nuevos y compone la secuencia completa desde la caché, sin descartar
frames anteriores. Reemplazar es una operación explícita. El estado se publica
solo al terminar la tarea, por lo que un fallo no elimina el material anterior.

Antes de exportar o reproducir se recomponen los frames si cambió la edición.
Pillow, la decodificación FFmpeg y las escrituras siguen fuera del hilo de Tk.
FFmpeg se ejecuta sin shell, con dos hilos, límite de frames/tiempo, proceso
oculto en Windows y limpieza de temporales. No se descargan dependencias.

Cancelar se comprueba entre frames, archivos y bloques USB. Un bloque cuyo tamaño
ya fue anunciado al dispositivo se termina antes de cancelar. Cerrar espera a la
tarea mediante la cola sin bloquear el main loop; libera el puerto al finalizar.

## Formato BM

La salida nueva usa el formato nativo **sin compresión**: un byte `00` seguido del
raster XBM, filas de `ceil(width/8)` bytes, bit menos significativo primero y
`1 = píxel negro`. Un frame 128×64 ocupa **1025 bytes**, no 1024.
Las dimensiones están en `meta.txt`. No requiere heatshrink para exportar.
El decodificador de esta base solo admite ese formato sin compresión.

La referencia oficial es
[scripts/flipper/assets/icon.py](https://github.com/flipperdevices/flipperzero-firmware/blob/dev/scripts/flipper/assets/icon.py).
La v1 conservada en `legacy` exportaba raster crudo de 1024 bytes. No se migran
ni reescriben automáticamente los packs existentes; reimpórtalos desde las
imágenes fuente para generar BM con cabecera.

## Animaciones

Se soportan secuencias pasivas en bucle e ida y vuelta. La segunda se expresa
mediante `Frames order`, sin campos de reproducción inventados. El orden editable
puede repetir índices. `Duration` es el tiempo durante el que Dolphin mantiene
esa animación antes de seleccionar otra; el firmware controla esa selección.
El GIF se convierte a FPS fijo elegidos por el usuario; no se conservan duraciones
variables por frame. Límite: 256 frames y 16 MP por imagen fuente.

Referencias:
[Dolphin assets](https://github.com/flipperdevices/flipperzero-firmware/blob/dev/assets/dolphin/ReadMe.md).

## USB

Se muestran todos los COM y se priorizan candidatos identificados por descripción
o VID/PID. El VID/PID no acredita identidad: antes de ejecutar storage se verifica
el modelo Flipper Zero. Las respuestas aceptan propiedades con puntos o guiones
bajos, espacios y códigos ANSI; se usa `device_info` con fallback a `info device`
y `!`. Se drena el posible segundo prompt producido por Ctrl+C/Enter antes de
consultar identidad, para no confundirlo con una respuesta vacía.
El transporte es CLI USB CDC, no RPC
protobuf. Un `RLock` protege la sesión.

`storage write_chunk` requiere esperar `Ready`, transmitir exactamente los bytes
anunciados y esperar el prompt. El comando añade datos: por eso esta base solo
carga en carpetas nuevas. Cada archivo se verifica con `storage md5`. MD5 aquí
comprueba errores de transferencia, no autenticidad criptográfica.

Las rutas se validan y entrecomillan; las escrituras se limitan a subcarpetas de
`/ext`. No hay borrado recursivo ni sobrescrituras automáticas. Una cancelación o
fallo puede dejar una carpeta parcial nueva; debe revisarse antes de reintentar.
Ante un timeout de protocolo se cierra la conexión para no reutilizar una sesión
que pudiera estar esperando datos binarios.

Referencia:
[storage_cli.c](https://github.com/flipperdevices/flipperzero-firmware/blob/dev/applications/services/storage/storage_cli.c).

## Alcance de esta entrega

Es una base para pruebas locales, no una versión final certificada con hardware.
El pack Momentum contiene una animación; aún no sustituye todos los iconos/fonts
del sistema. Incluye editor de dibujo y texto; aún no guarda proyectos persistentes.
La exportación Dolphin oficial produce un ZIP para instalación manual: combinar
su manifest con el existente exige revisión. El botón de carga directa del último
pack se habilita funcionalmente para Momentum; no altera el manifest global oficial.
La carga de archivos no activa automáticamente un pack en el firmware.

Los scripts de entrada anteriores redirigen a v2. El programa original completo
permanece en `legacy/Flipper_Assest_Maker_v1.py`, incluidos sus diagnósticos.
