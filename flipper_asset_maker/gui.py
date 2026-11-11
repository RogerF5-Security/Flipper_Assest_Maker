"""CustomTkinter desktop shell. Workers communicate exclusively through a queue."""

from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path, PurePosixPath
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import traceback

import customtkinter as ctk

from . import __version__
from .image_processor import ImageProcessor
from .models import AnimationSettings, ImageSettings, Stroke
from .media_loader import MediaLoader, export_gif
from .pack_exporter import PackExporter
from .numeric_field import NumericField
from .editor_tabs import EditorTabs
from .usb_manager import FlipperUSBManager

TARGETS = {"Momentum · Asset Pack": "momentum", "Momentum legacy · anims": "momentum_legacy",
           "Oficial · Dolphin (manual)": "official", "Fuente firmware · PNG": "firmware_source"}

ORANGE = "#FF8C00"
BG = "#101114"
PANEL = "#1B1D22"
MUTED = "#A0A4AE"


@dataclass
class EditorState:
    paths: tuple = ()
    frames: list = field(default_factory=list)
    previews: list = field(default_factory=list)
    index: int = 0
    playing: bool = False
    export: object = None
    dirty: bool = False
    playback_order: tuple = ()
    playback_index: int = 0
    sources: list = field(default_factory=list)
    strokes: list = field(default_factory=list)
    undo: list = field(default_factory=list)
    redo: list = field(default_factory=list)
    revision: int = 0
    canvas_created: bool = False
    preview: object = None
    preview_index: int = -1


class AppGUI(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        super().__init__()
        self.title("Flipper Asset Maker Pro | RogerF5")
        self.geometry("1260x820")
        self.minsize(1040, 700)
        self.configure(fg_color=BG)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.usb = FlipperUSBManager()
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.closing = False
        self.pages = {}
        self.nav = {}
        self.editors = {}
        self.timers = {}
        self.preview_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flipper-preview")
        self.preview_timers = {}
        self.preview_running = set()
        self.current = "assets"
        self._build_shell()
        self._build_editor("assets", False)
        self._build_editor("animations", True)
        self._build_usb()
        self._bind_shortcuts()
        self.show_page("assets")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(50, self._drain)
        self.after(150, self.scan_ports)

    def _button(self, parent, text, command, accent=False, **kwargs):
        return ctk.CTkButton(parent, text=text, command=command, height=36,
                            fg_color=ORANGE if accent else "#30333B",
                            text_color="#101114" if accent else "#FAFAFA",
                            hover_color="#E17800" if accent else "#41454F", **kwargs)

    def _build_shell(self):
        sidebar = ctk.CTkFrame(self, width=215, corner_radius=0, fg_color="#17191D")
        sidebar.grid(row=0, column=0, rowspan=3, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(6, weight=1)
        ctk.CTkLabel(sidebar, text="FLIPPER", font=("Segoe UI", 29, "bold"), text_color=ORANGE).grid(row=0, column=0, padx=24, pady=(28, 0), sticky="w")
        ctk.CTkLabel(sidebar, text="ASSET MAKER  /  PRO", font=("Segoe UI", 12, "bold"), text_color=MUTED).grid(row=1, column=0, padx=24, pady=(0, 34), sticky="w")
        for row, (key, title) in enumerate((("assets", "01   Creador de assets"), ("animations", "02   Animaciones"), ("usb", "03   Gestor USB")), 2):
            button = self._button(sidebar, title, lambda k=key: self.show_page(k), anchor="w", width=183)
            button.grid(row=row, column=0, padx=16, pady=5)
            self.nav[key] = button
        qa = ctk.CTkFrame(sidebar, fg_color="transparent")
        qa.grid(row=5, column=0, padx=16, pady=(24, 0), sticky="ew")
        self._button(qa, "Validar carpeta", lambda: self.validate_pack(False), width=183).pack(pady=4)
        self._button(qa, "Validar ZIP", lambda: self.validate_pack(True), width=183).pack(pady=4)
        ctk.CTkLabel(sidebar, text="128 × 64\n1-BIT WORKSPACE", text_color=MUTED, justify="left", font=("Consolas", 13)).grid(row=7, column=0, padx=24, pady=16, sticky="w")
        ctk.CTkLabel(sidebar, text=f"Creada por RogerF5\nv{__version__}", text_color=MUTED, justify="left", font=("Segoe UI", 11)).grid(row=8, column=0, padx=24, pady=(0, 24), sticky="w")

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=1, padx=24, pady=(24, 12), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        self.heading = ctk.CTkLabel(header, text="", font=("Segoe UI", 27, "bold"), anchor="w")
        self.heading.grid(row=0, column=0, sticky="w")
        self.connection = ctk.CTkLabel(header, text="USB · Desconectado", text_color=MUTED)
        self.connection.grid(row=0, column=1, padx=(12, 0))
        self.subtitle = ctk.CTkLabel(header, text="", text_color=MUTED, anchor="w")
        self.subtitle.grid(row=1, column=0, columnspan=2, pady=(4, 0), sticky="w")

        footer = ctk.CTkFrame(self, fg_color=PANEL)
        footer.grid(row=2, column=1, padx=24, pady=(8, 20), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(footer, text="Listo para crear.", anchor="w")
        self.status.grid(row=0, column=0, padx=16, pady=(10, 0), sticky="ew")
        self.cancel_button = self._button(footer, "Cancelar tarea", self.cancel.set, width=125)
        self.cancel_button.grid(row=0, column=1, rowspan=2, padx=12, pady=12)
        self.cancel_button.configure(state="disabled")
        self.progress = ctk.CTkProgressBar(footer, progress_color=ORANGE)
        self.progress.grid(row=1, column=0, padx=16, pady=(6, 12), sticky="ew")
        self.progress.set(0)

    def show_page(self, key):
        for page in self.pages.values():
            page.grid_remove()
        for button in self.nav.values():
            button.configure(fg_color="#30333B", text_color="#FAFAFA")
        self.current = key
        self.pages[key].grid(row=1, column=1, padx=24, pady=4, sticky="nsew")
        self.nav[key].configure(fg_color=ORANGE, text_color=BG)
        titles = {
            "assets": ("Creador de assets", "Transforma una imagen en un recurso monocromo para Flipper Zero."),
            "animations": ("Estudio de animaciones", "Importa un GIF o una secuencia; controla los frames y exporta tu pack."),
            "usb": ("Gestor USB", "Explora la SD y carga una carpeta nueva con verificación de integridad."),
        }
        title, subtitle = titles[key]
        self.heading.configure(text=title)
        self.subtitle.configure(text=subtitle)

    def _entry(self, parent, label, value):
        ctk.CTkLabel(parent, text=label, text_color=MUTED, anchor="w").pack(fill="x", padx=14, pady=(12, 2))
        variable = tk.StringVar(value=value)
        ctk.CTkEntry(parent, textvariable=variable, height=34).pack(fill="x", padx=14)
        return variable

    def _select(self, parent, label, values, callback=None):
        ctk.CTkLabel(parent, text=label, text_color=MUTED, anchor="w").pack(fill="x", padx=14, pady=(12, 2))
        variable = tk.StringVar(value=values[0])
        ctk.CTkOptionMenu(parent, values=values, variable=variable, command=callback,
                          fg_color="#30333B", button_color="#454954", button_hover_color="#555965").pack(fill="x", padx=14)
        return variable

    def _slider_field(self, parent, label, value, minimum, maximum, step=1):
        field = NumericField(parent, label, value, minimum, maximum, step)
        field.pack(fill="x", padx=14, pady=(10, 0))
        return field.value

    def _commit_numbers(self, key):
        for variable in self.editors[key].values():
            field = getattr(variable, "numeric_field", None)
            if field is not None:
                field.commit()

    def _editor_controls(self, parent, key, animation):
        tabs = EditorTabs(parent, fg_color=PANEL, segmented_button_selected_color="#9E5600",
                              segmented_button_selected_hover_color="#B36810", height=400)
        tabs.pack(fill="both", expand=True, padx=4, pady=(0, 6))
        panels = {}
        for title in ("Imagen", "Texto", "Dibujo", "Pack"):
            tab = tabs.add(title)
            panel = ctk.CTkScrollableFrame(tab, fg_color=PANEL, width=265)
            panel.pack(fill="both", expand=True)
            panels[title] = panel
        fields = dict(tabs=tabs)
        image = panels["Imagen"]
        fields["fit"] = self._select(image, "Encajar imagen", ["Contener", "Recortar", "Estirar"])
        fields["zoom"] = self._slider_field(image, "Tamaño (%)", 100, 5, 800)
        fields["offset_x"] = self._slider_field(image, "Posición X (px)", 0, -512, 512)
        fields["offset_y"] = self._slider_field(image, "Posición Y (px)", 0, -512, 512)
        self._button(image, "Centrar imagen", lambda: self.center_image(key)).pack(fill="x", padx=14, pady=(8, 4))
        self._button(image, "Ajustar a pantalla / restablecer", lambda: self.reset_transform(key)).pack(fill="x", padx=14, pady=4)
        fields["threshold"] = self._slider_field(image, "Umbral (sin dithering)", 128, 0, 255)
        fields["contrast"] = self._slider_field(image, "Contraste", 1.0, 0.1, 4.0, 0.1)
        for name, label, default in (("dither", "Dithering Floyd–Steinberg", True), ("invert", "Invertir colores", False)):
            fields[name] = tk.BooleanVar(value=default)
            ctk.CTkSwitch(image, text=label, variable=fields[name], progress_color=ORANGE).pack(anchor="w", padx=14, pady=8)
        self._button(image, "Aplicar a todos los frames", lambda: self.convert(key)).pack(fill="x", padx=14, pady=12)

        text = panels["Texto"]
        ctk.CTkLabel(text, text="Texto sobre imagen (o lienzo vacío)", text_color=MUTED).pack(anchor="w", padx=14, pady=(10, 4))
        fields["text_box"] = ctk.CTkTextbox(text, height=100, undo=True)
        fields["text_box"].pack(fill="x", padx=14)
        fields["text_box"].bind("<<Modified>>", lambda _: self._text_modified(key))
        fields["font"] = self._select(text, "Tipografía", ["Segoe UI", "Consolas", "Arial", "Pixel"])
        fields["font_size"] = self._slider_field(text, "Fuente (px)", 10, 6, 48)
        fields["text_x"] = self._slider_field(text, "Texto X (px)", 5, -512, 512)
        fields["text_y"] = self._slider_field(text, "Texto Y (px)", 5, -512, 512)
        fields["text_spacing"] = self._slider_field(text, "Interlineado", 2, 0, 20)
        for name, label in (("bold", "Negrita"), ("text_white", "Texto blanco"), ("text_center", "Anclar por el centro")):
            fields[name] = tk.BooleanVar(value=False)
            ctk.CTkSwitch(text, text=label, variable=fields[name], progress_color=ORANGE).pack(anchor="w", padx=14, pady=7)
        self._button(text, "Centrar texto en pantalla", lambda: self.center_text(key)).pack(fill="x", padx=14, pady=10)
        self._button(text, "Quitar texto", lambda: self.clear_text(key)).pack(fill="x", padx=14, pady=4)

        drawing = panels["Dibujo"]
        ctk.CTkLabel(drawing, text="Dibuja sobre la pantalla naranja.\nEl botón derecho siempre borra.\nEl dibujo se aplica a todos los frames.", text_color=MUTED, justify="left").pack(anchor="w", padx=14, pady=12)
        fields["brush"] = self._slider_field(drawing, "Grosor del pincel (px)", 2, 1, 16)
        for title, tool in (("Lápiz negro", "Lápiz"), ("Pincel blanco", "Blanco"), ("Borrador (revela imagen)", "Borrador")):
            self._button(drawing, title, lambda t=tool: self.editors[key]["tool"].set(t)).pack(fill="x", padx=14, pady=4)
        fields["undo_button"] = self._button(drawing, "Deshacer · Ctrl+Z", lambda: self.undo_drawing(key))
        fields["undo_button"].pack(fill="x", padx=14, pady=(16, 4))
        fields["redo_button"] = self._button(drawing, "Rehacer · Ctrl+Y", lambda: self.undo_drawing(key, True))
        fields["redo_button"].pack(fill="x", padx=14, pady=4)
        fields["clear_button"] = self._button(drawing, "Limpiar dibujo", lambda: self.clear_drawing(key))
        fields["clear_button"].pack(fill="x", padx=14, pady=10)
        fields["history_label"] = ctk.CTkLabel(drawing, text="", text_color=MUTED, wraplength=230)
        fields["history_label"].pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(drawing, text="B: lápiz · E: borrador · W: blanco\n[ / ]: grosor · Esc: cancelar trazo\nCtrl+Shift+Z también rehace", text_color=MUTED, justify="left", wraplength=230).pack(fill="x", padx=14, pady=8)

        pack = panels["Pack"]
        fields["pack"] = self._entry(pack, "Nombre del pack", "RogerF5")
        fields["name"] = self._entry(pack, "Nombre de la animación", "MyAnimation")
        fields["fps"] = self._entry(pack, "Fotogramas por segundo (1–30)", "8")
        fields["duration"] = self._entry(pack, "Duración Dolphin (segundos)", "60")
        fields["playback"] = self._select(pack, "Reproducción", ["Bucle", "Ida y vuelta"])
        fields["order"] = self._entry(pack, "Orden: 0 1 2… (vacío = secuencial)", "")
        fields["target"] = self._select(pack, "Formato de instalación", list(TARGETS))
        for name, label, value in (("min_butthurt", "Butthurt mínimo", "0"), ("max_butthurt", "Butthurt máximo", "14"),
                                   ("min_level", "Nivel mínimo", "1"), ("max_level", "Nivel máximo", "3"), ("weight", "Peso de selección", "3")):
            fields[name] = self._entry(pack, label, value)
        fields["max_frames"] = self._entry(pack, "Máximo de frames al importar", "120")
        fields["frame_step"] = self._entry(pack, "Tomar uno de cada N frames", "1")
        fields["video_start"] = self._entry(pack, "Vídeo: inicio (segundos)", "0")
        fields["video_duration"] = self._entry(pack, "Vídeo: fragmento (segundos)", "8")
        self._button(pack, "Reimportar con estos límites", lambda: self.convert(key, self.editors[key]["state"].paths)).pack(fill="x", padx=14, pady=12)
        return fields

    def _build_editor(self, key, animation):
        state = EditorState()
        page = ctk.CTkFrame(self, fg_color="transparent")
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(0, weight=1)
        self.pages[key] = page
        controls = ctk.CTkFrame(page, width=310, fg_color=PANEL)
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        controls.grid_propagate(False)
        ctk.CTkLabel(controls, text="FUENTE Y CONVERSIÓN", font=("Segoe UI", 12, "bold"), text_color=ORANGE).pack(anchor="w", padx=14, pady=(14, 12))
        self._button(controls, "Agregar imágenes / GIF / vídeo", lambda: self.import_media(key), True).pack(fill="x", padx=14)
        source_actions = ctk.CTkFrame(controls, fg_color="transparent")
        source_actions.pack(fill="x", padx=14, pady=(6, 0))
        source_actions.grid_columnconfigure((0, 1), weight=1)
        self._button(source_actions, "Reemplazar", lambda: self.import_media(key, replace=True), width=120).grid(row=0, column=0, padx=(0, 3), sticky="ew")
        self._button(source_actions, "Lienzo vacío", lambda: self.new_canvas(key), width=120).grid(row=0, column=1, padx=(3, 0), sticky="ew")
        source_label = ctk.CTkLabel(controls, text="Sin archivos seleccionados", text_color=MUTED, wraplength=230, justify="left")
        source_label.pack(fill="x", padx=14, pady=8)
        fields = self._editor_controls(controls, key, animation)
        right = ctk.CTkFrame(page, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        preview_tools = ctk.CTkFrame(right, fg_color="transparent")
        preview_tools.grid(row=0, column=0, sticky="ew", pady=(4, 8))
        preview_tools.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(preview_tools, text="VISTA PREVIA", text_color=MUTED, font=("Consolas", 12)).grid(row=0, column=0, sticky="w")
        preview_undo = self._button(preview_tools, "Deshacer", lambda: self.undo_drawing(key), width=85)
        preview_undo.grid(row=0, column=1, padx=4)
        preview_redo = self._button(preview_tools, "Rehacer", lambda: self.undo_drawing(key, True), width=85)
        preview_redo.grid(row=0, column=2, padx=4)
        self._button(preview_tools, "?", self.show_shortcuts, width=30).grid(row=0, column=3)
        screen_frame = ctk.CTkFrame(right, fg_color="#282B31", border_color="#42464F", border_width=2, corner_radius=18)
        screen_frame.grid(row=1, column=0, sticky="nsew")
        screen_frame.grid_rowconfigure(0, weight=1)
        screen_frame.grid_columnconfigure(0, weight=1)
        canvas = tk.Canvas(screen_frame, bg="#282B31", highlightthickness=0, width=128, height=64)
        canvas.grid(row=0, column=0, padx=16, pady=16, sticky="nsew")
        info = ctk.CTkLabel(right, text="128 × 64 px   •   Monocromo puro   •   #FF8C00", text_color=MUTED, font=("Consolas", 11))
        info.grid(row=2, column=0, pady=(6, 2))
        quick = ctk.CTkFrame(right, fg_color=PANEL)
        quick.grid(row=3, column=0, sticky="ew", pady=(2, 8))
        quick.grid_columnconfigure(0, weight=1)
        tool = tk.StringVar(value="Mover imagen")
        ctk.CTkOptionMenu(quick, variable=tool, values=["Mover imagen", "Mover texto", "Lápiz", "Blanco", "Borrador"],
                          fg_color="#30333B", width=135, command=lambda _: self._tool_cursor(key)).grid(row=0, column=0, padx=8, pady=8, sticky="ew")
        for column, (label, dx, dy) in enumerate((("←", -1, 0), ("↑", 0, -1), ("↓", 0, 1), ("→", 1, 0)), 1):
            self._button(quick, label, lambda x=dx, y=dy: self.nudge(key, x, y), width=34).grid(row=0, column=column, padx=2)
        self._button(quick, "Centrar", lambda: self.center_active(key), width=65).grid(row=0, column=5, padx=4)
        self._button(quick, "−", lambda: self.adjust_zoom(key, -5), width=34).grid(row=1, column=1, pady=(0, 6))
        self._button(quick, "+", lambda: self.adjust_zoom(key, 5), width=34).grid(row=1, column=2, pady=(0, 6))
        ctk.CTkLabel(quick, text="Arrastra · rueda: tamaño\nFlechas: 1 px / Shift: 8 px", text_color=MUTED, font=("Segoe UI", 10), wraplength=160).grid(row=1, column=3, columnspan=3, padx=6, sticky="w")
        timeline = ctk.CTkFrame(right, fg_color=PANEL)
        timeline.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        timeline.grid_columnconfigure(2, weight=1)
        play = self._button(timeline, "▶", lambda: self.toggle_play(key), width=44)
        play.grid(row=0, column=0, padx=(8, 4), pady=8)
        previous = self._button(timeline, "<", lambda: self.step_frame(key, -1), width=28)
        previous.grid(row=0, column=1, padx=2)
        slider = ctk.CTkSlider(timeline, from_=0, to=1, number_of_steps=1, progress_color=ORANGE,
                              command=lambda value: self.select_frame(key, value))
        slider.grid(row=0, column=2, sticky="ew", padx=6)
        slider.set(0)
        counter = ctk.CTkLabel(timeline, text="0 / 0", width=80)
        following = self._button(timeline, ">", lambda: self.step_frame(key, 1), width=28)
        following.grid(row=0, column=3, padx=2)
        counter.grid(row=0, column=4, padx=8)
        actions = ctk.CTkFrame(right, fg_color=PANEL)
        actions.grid(row=5, column=0, sticky="ew")
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)
        if animation:
            self._button(actions, "Exportar pack + ZIP", lambda: self.export_pack(key), True).grid(row=0, column=0, padx=14, pady=(14, 6), sticky="ew")
            self._button(actions, "Cargar último pack por USB", lambda: self.push_export(key)).grid(row=1, column=0, columnspan=2, padx=14, pady=6, sticky="ew")
            text = "El pack se exporta como carpeta nueva. Su activación depende del firmware."
        else:
            self._button(actions, "Exportar imagen .bm / .xbm", lambda: self.export_image(key), True).grid(row=0, column=0, padx=14, pady=(14, 6), sticky="ew")
            self._button(actions, "Exportar pack + ZIP", lambda: self.export_pack(key)).grid(row=1, column=0, padx=14, pady=6, sticky="ew")
            self._button(actions, "Cargar pack por USB", lambda: self.push_export(key)).grid(row=1, column=1, padx=(0, 14), pady=6, sticky="ew")
            text = "BM nativo sin compresión. XBM disponible como código fuente C."
        self._button(actions, "Exportar GIF editado", lambda: self.export_animation_gif(key)).grid(row=0, column=1, padx=(0, 14), pady=(14, 6), sticky="ew")
        editor = dict(state=state, canvas=canvas, info=info, source=source_label, slider=slider, counter=counter,
                      play=play, tool=tool, preview_undo=preview_undo, preview_redo=preview_redo,
                      previous_button=previous, next_button=following, **fields)
        self.editors[key] = editor
        tool.trace_add("write", lambda *_: self._tool_cursor(key))
        for name in ("pack", "name", "fps", "duration", "playback", "order", "target", "min_butthurt", "max_butthurt", "min_level", "max_level", "weight"):
            editor[name].trace_add("write", lambda *_args, k=key: self._invalidate_export(k))
        canvas.bind("<Configure>", lambda _: self.render(key))
        editor["brush"].trace_add("write", lambda *_: self._brush_changed(key))
        for variable in (editor[name] for name in ("fit", "zoom", "offset_x", "offset_y", "threshold", "contrast", "dither", "invert",
                                                   "font", "font_size", "text_x", "text_y", "text_spacing", "bold", "text_white", "text_center")):
            variable.trace_add("write", lambda *_args, k=key: self.mark_dirty(k))
        canvas.configure(takefocus=True, cursor="fleur")
        canvas.bind("<ButtonPress-1>", lambda event: self.canvas_press(key, event))
        canvas.bind("<B1-Motion>", lambda event: self.canvas_motion(key, event))
        canvas.bind("<ButtonRelease-1>", lambda event: self.canvas_release(key, event))
        canvas.bind("<ButtonPress-3>", lambda event: self.canvas_press(key, event, True))
        canvas.bind("<B3-Motion>", lambda event: self.canvas_motion(key, event))
        canvas.bind("<ButtonRelease-3>", lambda event: self.canvas_release(key, event))
        canvas.bind("<MouseWheel>", lambda event: self.adjust_zoom(key, 5 if event.delta > 0 else -5))
        for arrow, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            canvas.bind(f"<{arrow}>", lambda event, x=dx, y=dy: self.nudge(key, x, y, 8 if event.state & 1 else 1))
        canvas.bind("<Motion>", lambda event: self.brush_cursor(key, event))
        canvas.bind("<Leave>", lambda _: self.brush_cursor(key, None))
        for sequence, redo in (("<Control-z>", False), ("<Control-y>", True), ("<Control-Shift-Z>", True)):
            editor["text_box"].bind(sequence, lambda _event, r=redo: self._undo_text(key, r))
        self._update_drawing_ui(key)

    def mark_dirty(self, key):
        editor = self.editors.get(key)
        if not editor or self.closing:
            return
        state = editor["state"]
        self._stop_playback(key)
        state.dirty = True
        state.export = None
        state.revision += 1
        self._update_drawing_ui(key)
        self._schedule_preview(key)

    def _bind_shortcuts(self):
        self.bind("<Control-z>", lambda event: self._history_shortcut(event, False))
        self.bind("<Control-y>", lambda event: self._history_shortcut(event, True))
        self.bind("<Control-Shift-Z>", lambda event: self._history_shortcut(event, True))
        self.bind("<KeyPress>", self._editor_keypress)
        self.bind("<F1>", lambda _event: self.show_shortcuts())

    def _shortcut_editor(self, event):
        if self.closing or self.current not in self.editors:
            return None
        widget = event.widget
        if widget.winfo_toplevel() != self:
            return None
        if widget.winfo_class() in {"Entry", "Text", "Spinbox", "TEntry", "TCombobox", "TSpinbox", "Menu", "Listbox"}:
            return None
        return self.current

    def _history_shortcut(self, event, redo):
        key = self._shortcut_editor(event)
        if key is not None:
            return self.undo_drawing(key, redo)

    def _undo_text(self, key, redo=False):
        try:
            box = self.editors[key]["text_box"]
            (box.edit_redo if redo else box.edit_undo)()
        except tk.TclError:
            pass  # No text edits to undo/redo. Never consume drawing history.
        return "break"

    def _editor_keypress(self, event):
        key = self._shortcut_editor(event)
        if key is None:
            return
        symbol = event.keysym.lower()
        symbol = {"[": "bracketleft", "]": "bracketright"}.get(symbol, symbol)
        # Spanish keyboards need AltGr (reported as Ctrl+Alt) for [ and ].
        if event.state & (4 | 8 | 0x20000) and symbol not in {"bracketleft", "bracketright"}:
            return
        editor = self.editors[key]
        tools = {"b": "Lápiz", "e": "Borrador", "w": "Blanco", "v": "Mover imagen", "t": "Mover texto"}
        if symbol in tools:
            self.cancel_gesture(key)
            editor["tool"].set(tools[symbol])
            if symbol in {"b", "e", "w"}:
                editor["tabs"].set("Dibujo")
            editor["canvas"].focus_set()
        elif symbol in {"bracketleft", "bracketright"}:
            amount = -1 if symbol == "bracketleft" else 1
            editor["brush"].set(max(1, min(16, editor["brush"].get() + amount)))
        elif symbol == "escape":
            self.cancel_gesture(key)
        elif symbol in {"prior", "next", "home", "end"}:
            if symbol in {"prior", "next"}:
                self.step_frame(key, -1 if symbol == "prior" else 1)
            else:
                self.select_frame(key, 0 if symbol == "home" else len(editor["state"].frames) - 1)
        elif symbol == "space":
            self.toggle_play(key)
        else:
            return
        return "break"

    def show_shortcuts(self):
        window = getattr(self, "shortcuts_window", None)
        if window is not None and window.winfo_exists():
            window.lift()
            return "break"
        window = self.shortcuts_window = ctk.CTkToplevel(self)
        window.title("Atajos del editor")
        window.geometry("490x430")
        window.transient(self)
        ctk.CTkLabel(window, text="ATAJOS DEL EDITOR", font=("Segoe UI", 20, "bold"), text_color=ORANGE).pack(padx=24, pady=(24, 14))
        ctk.CTkLabel(window, text="Ctrl+Z   Deshacer dibujo\nCtrl+Y / Ctrl+Shift+Z   Rehacer dibujo\nB / E / W   Lápiz / Borrador / Blanco\nV / T   Mover imagen / Mover texto\n[ / ]   Reducir / aumentar pincel\nEsc   Cancelar el trazo o arrastre actual\nEspacio   Reproducir / pausar\nRePág / AvPág   Frame anterior / siguiente\nInicio / Fin   Primer / último frame\nFlechas / Shift+flechas   Mover 1 / 8 píxeles", justify="left", font=("Segoe UI", 13)).pack(padx=24, pady=8)
        ctk.CTkLabel(window, text="Al escribir texto, Ctrl+Z/Y actúan sobre el texto.\nLos campos de entrada conservan su teclado normal.", text_color=MUTED).pack(padx=24, pady=12)
        self._button(window, "Cerrar", window.destroy).pack(pady=(0, 18))
        window.bind("<Escape>", lambda _: window.destroy())
        return "break"

    def _invalidate_export(self, key):
        self.editors[key]["state"].export = None

    def _text_modified(self, key):
        editor = self.editors.get(key)
        if editor and editor["text_box"].edit_modified():
            editor["text_box"].edit_modified(False)
            self.mark_dirty(key)

    def _schedule_preview(self, key):
        if key not in self.preview_timers and not self.closing:
            self.preview_timers[key] = self.after(80, lambda: self._preview(key))

    def _preview(self, key):
        self.preview_timers.pop(key, None)
        if self.closing or key in self.preview_running:
            return
        editor = self.editors[key]
        state = editor["state"]
        try:
            settings = self._image_settings(key)
        except (ValueError, tk.TclError):
            return  # A partially typed numeric field is not an error dialog.
        if not state.sources and not (settings.text or state.strokes or state.canvas_created):
            return
        revision, index = state.revision, state.index
        source = state.sources[index] if state.sources else None
        self.preview_running.add(key)

        def work():
            try:
                from PIL import Image
                image = ImageProcessor.process(source if source is not None else Image.new("RGBA", (128, 64), "white"), settings)
                data = self._preview_bytes(image)
                self.events.put(("preview", (key, revision, index, data, None)))
            except Exception as error:
                self.events.put(("preview", (key, revision, index, None, str(error))))
        self.preview_pool.submit(work)

    @staticmethod
    def _preview_bytes(frame):
        stream = io.BytesIO()
        ImageProcessor.preview(frame, 1).save(stream, format="PPM")
        return stream.getvalue()

    def _image_settings(self, key):
        editor = self.editors[key]
        return ImageSettings(fit={"Contener": "contain", "Recortar": "cover", "Estirar": "stretch"}[editor["fit"].get()],
                             dither=editor["dither"].get(), invert=editor["invert"].get(),
                             threshold=int(editor["threshold"].get()), contrast=float(editor["contrast"].get()),
                             zoom=editor["zoom"].get() / 100, offset_x=editor["offset_x"].get(), offset_y=editor["offset_y"].get(),
                             text=editor["text_box"].get("1.0", "end-1c"), text_x=editor["text_x"].get(), text_y=editor["text_y"].get(),
                             font=editor["font"].get(), font_size=editor["font_size"].get(), bold=editor["bold"].get(),
                             text_white=editor["text_white"].get(), text_spacing=editor["text_spacing"].get(),
                             text_center=editor["text_center"].get(), strokes=tuple(editor["state"].strokes))

    def _animation_settings(self, key):
        editor = self.editors[key]
        return AnimationSettings(name=editor["name"].get(), pack=editor["pack"].get(), fps=int(editor["fps"].get()),
                                 duration=int(editor["duration"].get()),
                                 playback="loop" if editor["playback"].get() == "Bucle" else "pingpong",
                                 order=tuple(map(int, editor["order"].get().replace(",", " ").split())),
                                 target=TARGETS[editor["target"].get()],
                                 **{name: int(editor[name].get()) for name in ("min_butthurt", "max_butthurt", "min_level", "max_level", "weight")})

    def import_media(self, key, replace=False):
        if self.busy:
            return
        selected = filedialog.askopenfilenames(parent=self, title="Agregar imágenes, GIF o vídeo" if not replace else "Reemplazar imágenes de la secuencia", filetypes=[("Imagen / GIF / vídeo", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.xbm *.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mpg *.mpeg"), ("Todos", "*.*")])
        if selected:
            self.convert(key, tuple(selected), append=not replace)

    def convert(self, key, paths=None, on_done=None, append=False):
        if self.busy or self.closing:
            return
        editor = self.editors[key]
        state = editor["state"]
        self._commit_numbers(key)
        try:
            settings = self._image_settings(key)
            options = dict(fps=int(editor["fps"].get()), max_frames=int(editor["max_frames"].get()),
                           step=int(editor["frame_step"].get()), start=float(editor["video_start"].get()),
                           duration=float(editor["video_duration"].get()))
        except (ValueError, tk.TclError) as error:
            self._error(str(error))
            return
        reload = bool(paths)
        sources = tuple(state.sources)
        appending = reload and append and bool(state.paths)
        previous_paths = state.paths
        paths = tuple(paths) if reload else previous_paths
        if appending:
            remaining = ImageProcessor.MAX_FRAMES - len(sources)
            if remaining < 1:
                self._error("La secuencia ya tiene 256 frames. Usa Reemplazar para iniciar otra selección.")
                return
            options["max_frames"] = min(options["max_frames"], remaining)
        if not reload and not sources and not (settings.text or state.strokes or state.canvas_created):
            self._error("Importa una imagen o vídeo, crea un lienzo o escribe un texto.")
            return
        previous_index = state.index

        def work(progress, cancel):
            from PIL import Image
            loaded = MediaLoader.load(paths, progress=progress, cancel=cancel, **options) if reload else list(sources)
            if appending:
                loaded = list(sources) + loaded
            if not loaded:
                loaded = [Image.new("RGBA", (128, 64), "white")]
            frames = []
            previews = []
            for index, source in enumerate(loaded):
                if cancel.is_set():
                    raise InterruptedError("Conversión cancelada.")
                frame = ImageProcessor.process(source, settings)
                frames.append(frame)
                previews.append(self._preview_bytes(frame))
                progress((index + 1) / len(loaded), f"Componiendo frame {index + 1}/{len(loaded)}")
            return loaded, frames, previews

        def done(result):
            state.sources, state.frames, state.previews = result
            state.paths = previous_paths + tuple(paths) if appending else tuple(paths)
            state.canvas_created = True
            # Invalidate preview jobs which still reference the previous source.
            state.index = len(sources) if appending else (0 if reload else min(previous_index, len(state.frames) - 1))
            state.preview = None
            state.preview_index = -1
            try:
                state.dirty = self._image_settings(key) != settings
            except (ValueError, tk.TclError):
                state.dirty = True
            state.revision += 1
            state.export = None
            count = len(state.frames)
            editor["source"].configure(text=f"{len(state.paths)} archivo(s) · {count} frame(s)\n{Path(paths[-1]).name if paths else 'Lienzo de texto / dibujo'}")
            editor["slider"].configure(to=max(1, count - 1), number_of_steps=max(1, count - 1))
            editor["slider"].set(state.index)
            self.render(key)
            if state.dirty:
                self._schedule_preview(key)
            elif on_done:
                on_done()
        self._submit("Convirtiendo imágenes…", work, done)

    def render(self, key):
        editor = self.editors[key]
        canvas = editor["canvas"]
        state = editor["state"]
        width, height = canvas.winfo_width(), canvas.winfo_height()
        scale = max(1, min(8, (width - 8) // 128, (height - 8) // 64))
        editor["screen_rect"] = ((width - 128 * scale) // 2, (height - 64 * scale) // 2, scale)
        canvas.delete("all")
        data = state.preview if state.preview_index == state.index else (state.previews[state.index] if state.previews else None)
        if data:
            # Only Tk rendering happens here. All Pillow conversion lives in the worker.
            native = tk.PhotoImage(data=data, format="PPM", master=self)
            editor["photo"] = native.zoom(scale, scale)
            canvas.create_image(width // 2, height // 2, image=editor["photo"])
            editor["counter"].configure(text=f"{state.index + 1} / {max(1,len(state.sources))}")
        else:
            canvas.create_rectangle((width - 128 * scale) // 2, (height - 64 * scale) // 2,
                                    (width + 128 * scale) // 2, (height + 64 * scale) // 2, fill=ORANGE, outline="")
            canvas.create_text(width // 2, height // 2, text="IMPORTA TU PRIMER ASSET", fill=BG, font=("Consolas", 13, "bold"))
        editor["info"].configure(text=f"128 × 64 px   •   Zoom {scale}×   •   1 bit / píxel")
        self._update_drawing_ui(key)
        self._paint_brush_cursor(key)

    def select_frame(self, key, value):
        state = self.editors[key]["state"]
        if state.frames:
            self._stop_playback(key)
            self.cancel_gesture(key)
            state.index = min(len(state.frames) - 1, max(0, round(float(value))))
            self.editors[key]["slider"].set(state.index)
            self.render(key)
            if state.dirty:
                self._schedule_preview(key)

    def step_frame(self, key, amount):
        self.select_frame(key, self.editors[key]["state"].index + amount)

    def _update_drawing_ui(self, key):
        editor = self.editors.get(key)
        if not editor:
            return
        state = editor["state"]
        active = "stroke" in editor or "drag" in editor

        def enabled(button, available):
            value = "normal" if available else "disabled"
            if button.cget("state") != value:
                button.configure(state=value)

        for name in ("undo_button", "preview_undo"):
            enabled(editor[name], state.undo or active)
        for name in ("redo_button", "preview_redo"):
            enabled(editor[name], state.redo and not active)
        enabled(editor["clear_button"], state.strokes and not active)
        label = f"{len(state.strokes)} trazos · Pincel {editor['brush'].get()} px\nDeshacer: {len(state.undo)} · Rehacer: {len(state.redo)}"
        if editor["history_label"].cget("text") != label:
            editor["history_label"].configure(text=label)
        enabled(editor["previous_button"], state.frames and state.index > 0)
        enabled(editor["next_button"], state.index + 1 < len(state.frames))

    def _remember_drawing(self, state):
        state.undo.append(tuple(state.strokes))
        del state.undo[:-50]
        state.redo.clear()

    def cancel_gesture(self, key):
        editor = self.editors[key]
        stroke = editor.pop("stroke", None)
        drag = editor.pop("drag", None)
        if drag:
            _origin, names, initial = drag
            editor[names[0]].set(initial[0])
            editor[names[1]].set(initial[1])
        if stroke or drag:
            self.render(key)
            return True
        return False

    def brush_cursor(self, key, event):
        editor = self.editors[key]
        point = self._canvas_point(key, event) if event is not None else None
        if point is None:
            editor.pop("pointer", None)
        else:
            editor["pointer"] = point
        self._paint_brush_cursor(key)

    def _paint_brush_cursor(self, key):
        editor = self.editors[key]
        canvas = editor["canvas"]
        canvas.delete("brush_cursor")
        point = editor.get("pointer")
        if point is None or (editor["tool"].get().startswith("Mover") and "stroke" not in editor):
            return
        x, y, scale = editor.get("screen_rect", (0, 0, 1))
        radius = editor["brush"].get() * scale / 2
        cx, cy = x + (point[0] + .5) * scale, y + (point[1] + .5) * scale
        canvas.create_oval(cx-radius, cy-radius, cx+radius, cy+radius, outline="#36B8D9", width=1, tags="brush_cursor")

    def _brush_changed(self, key):
        self._update_drawing_ui(key)
        self._paint_brush_cursor(key)

    def _tool_cursor(self, key):
        editor = self.editors[key]
        editor["canvas"].configure(cursor="fleur" if editor["tool"].get().startswith("Mover") else "crosshair")
        self._paint_brush_cursor(key)

    def new_canvas(self, key):
        if self.busy:
            return
        state = self.editors[key]["state"]
        if (state.sources or state.strokes or self.editors[key]["text_box"].get("1.0", "end-1c")) and not messagebox.askyesno("Nuevo lienzo", "¿Reemplazar el contenido actual de este módulo?", parent=self):
            return
        state.paths = ()
        state.sources = []
        state.frames = []
        state.previews = []
        state.strokes.clear()
        state.undo.clear()
        state.redo.clear()
        state.index = 0
        state.canvas_created = True
        self.clear_text(key)
        self.reset_transform(key)
        self.mark_dirty(key)
        self.convert(key)

    def center_image(self, key):
        editor = self.editors[key]
        editor["offset_x"].set(0)
        editor["offset_y"].set(0)

    def reset_transform(self, key):
        self.center_image(key)
        self.editors[key]["zoom"].set(100)
        self.editors[key]["fit"].set("Contener")

    def center_text(self, key):
        editor = self.editors[key]
        editor["text_center"].set(True)
        editor["text_x"].set(64)
        editor["text_y"].set(32)

    def clear_text(self, key):
        self.editors[key]["text_box"].delete("1.0", "end")
        self.mark_dirty(key)

    def center_active(self, key):
        if self.editors[key]["tool"].get() == "Mover texto":
            self.center_text(key)
        else:
            self.center_image(key)

    def nudge(self, key, dx, dy, amount=1):
        editor = self.editors[key]
        x_name, y_name = ("text_x", "text_y") if editor["tool"].get() == "Mover texto" else ("offset_x", "offset_y")
        try:
            editor[x_name].set(max(-512, min(512, editor[x_name].get() + dx * amount)))
            editor[y_name].set(max(-512, min(512, editor[y_name].get() + dy * amount)))
        except tk.TclError:
            return
        return "break"

    def adjust_zoom(self, key, amount):
        editor = self.editors[key]
        try:
            if editor["tool"].get() == "Mover texto":
                editor["font_size"].set(max(6, min(48, editor["font_size"].get() + (1 if amount > 0 else -1))))
            else:
                editor["zoom"].set(max(5, min(800, editor["zoom"].get() + amount)))
        except tk.TclError:
            return
        return "break"

    def _canvas_point(self, key, event, clamp=False):
        x, y, scale = self.editors[key].get("screen_rect", (0, 0, 1))
        px, py = (event.x - x) // scale, (event.y - y) // scale
        if clamp:
            return max(0, min(127, px)), max(0, min(63, py))
        return (px, py) if 0 <= px < 128 and 0 <= py < 64 else None

    def canvas_press(self, key, event, erase=False):
        point = self._canvas_point(key, event)
        if point is None:
            return
        editor = self.editors[key]
        editor["canvas"].focus_set()
        self._stop_playback(key)
        tool = "Borrador" if erase else editor["tool"].get()
        if tool.startswith("Mover"):
            names = ("text_x", "text_y") if tool == "Mover texto" else ("offset_x", "offset_y")
            try:
                editor["drag"] = (point, names, (editor[names[0]].get(), editor[names[1]].get()))
            except tk.TclError:
                return
        else:
            try:
                width = int(editor["brush"].get())
            except tk.TclError:
                return
            editor["stroke"] = dict(tool={"Lápiz": "black", "Blanco": "white", "Borrador": "eraser"}[tool], points=[point], width=width)
            x, y, scale = editor.get("screen_rect", (0, 0, 1))
            radius = width * scale / 2
            cx, cy = x + (point[0] + .5) * scale, y + (point[1] + .5) * scale
            editor["canvas"].create_oval(cx-radius, cy-radius, cx+radius, cy+radius,
                                         fill="black" if tool == "Lápiz" else ORANGE, outline="")
        self._update_drawing_ui(key)
        self.brush_cursor(key, event)

    def canvas_motion(self, key, event):
        editor = self.editors[key]
        if "drag" in editor:
            origin, names, initial = editor["drag"]
            x, y, scale = editor.get("screen_rect", (0, 0, 1))
            point = ((event.x-x)//scale, (event.y-y)//scale)
            editor[names[0]].set(max(-512, min(512, initial[0] + point[0] - origin[0])))
            editor[names[1]].set(max(-512, min(512, initial[1] + point[1] - origin[1])))
        elif "stroke" in editor:
            stroke = editor["stroke"]
            point = self._canvas_point(key, event, clamp=True)
            previous = stroke["points"][-1]
            if previous != point:
                stroke["points"].append(point)
                x, y, scale = editor.get("screen_rect", (0, 0, 1))
                color = "black" if stroke["tool"] == "black" else ORANGE
                editor["canvas"].create_line(x + (previous[0] + .5) * scale, y + (previous[1] + .5) * scale,
                                             x + (point[0] + .5) * scale, y + (point[1] + .5) * scale,
                                             fill=color, width=stroke["width"] * scale, capstyle="round")
        self.brush_cursor(key, event)

    def canvas_release(self, key, event):
        editor = self.editors[key]
        editor.pop("drag", None)
        stroke = editor.pop("stroke", None)
        if stroke:
            state = editor["state"]
            self._remember_drawing(state)
            state.strokes.append(Stroke(stroke["tool"], tuple(stroke["points"]), stroke["width"]))
            state.canvas_created = True
            self.mark_dirty(key)
        self._update_drawing_ui(key)

    def undo_drawing(self, key, redo=False):
        if not redo and self.cancel_gesture(key):
            return "break"
        if "stroke" in self.editors[key] or "drag" in self.editors[key]:
            return "break"
        state = self.editors[key]["state"]
        source, target = (state.redo, state.undo) if redo else (state.undo, state.redo)
        if source:
            target.append(tuple(state.strokes))
            del target[:-50]
            state.strokes = list(source.pop())
            self.mark_dirty(key)
        self._update_drawing_ui(key)
        return "break"

    def clear_drawing(self, key):
        self.cancel_gesture(key)
        state = self.editors[key]["state"]
        if state.strokes:
            self._remember_drawing(state)
            state.strokes.clear()
            self.mark_dirty(key)

    def _stop_playback(self, key):
        editor = self.editors[key]
        editor["state"].playing = False
        editor["play"].configure(text="▶")
        timer = self.timers.pop(key, None)
        if timer:
            self.after_cancel(timer)

    def toggle_play(self, key):
        editor = self.editors[key]
        state = editor["state"]
        if state.playing:
            self._stop_playback(key)
            return
        if not state.frames or self.busy:
            return
        if state.dirty:
            self.convert(key, on_done=lambda: self.toggle_play(key))
            return
        try:
            settings = self._animation_settings(key)
            state.playback_order = settings.sequence(len(state.frames))
        except ValueError as error:
            self._error(str(error))
            return
        state.playback_index = 0
        state.playing = True
        editor["play"].configure(text="Ⅱ")

        def tick():
            if state.playing and not self.closing:
                state.index = state.playback_order[state.playback_index]
                state.playback_index = (state.playback_index + 1) % len(state.playback_order)
                editor["slider"].set(state.index)
                self.render(key)
                self.timers[key] = self.after(max(1, round(1000 / settings.fps)), tick)
        tick()

    def _ready(self, key):
        state = self.editors[key]["state"]
        if not state.frames:
            self._error("Importa y convierte una imagen primero.")
            return False
        if state.dirty:
            self._error("Aplica la conversión para usar los últimos ajustes.")
            return False
        return not self.busy

    def export_image(self, key):
        if self.editors[key]["state"].dirty and not self.busy:
            self.convert(key, on_done=lambda: self.export_image(key))
            return
        if not self._ready(key):
            return
        selected = filedialog.asksaveasfilename(parent=self, title="Exportar asset", defaultextension=".bm", filetypes=[("Flipper Bitmap", "*.bm"), ("X Bitmap C", "*.xbm")])
        if not selected:
            return
        path = Path(selected)
        frame = self.editors[key]["state"].frames[self.editors[key]["state"].index]

        def work(progress, cancel):
            if path.suffix.lower() == ".bm":
                path.write_bytes(ImageProcessor.to_bm(frame))
            elif path.suffix.lower() == ".xbm":
                path.write_text(ImageProcessor.to_xbm(frame, path.stem), encoding="ascii")
            else:
                raise ValueError("Selecciona .bm o .xbm.")
            return path
        self._submit("Exportando asset…", work, lambda result: messagebox.showinfo("Asset exportado", str(result), parent=self))

    def export_pack(self, key):
        if self.editors[key]["state"].dirty and not self.busy:
            self.convert(key, on_done=lambda: self.export_pack(key))
            return
        if not self._ready(key):
            return
        try:
            settings = self._animation_settings(key)
            settings.sequence(len(self.editors[key]["state"].frames))
        except ValueError as error:
            self._error(str(error))
            return
        folder = filedialog.askdirectory(parent=self, title="Carpeta de salida (se creará un pack nuevo)")
        if not folder:
            return
        frames = tuple(self.editors[key]["state"].frames)

        def done(result):
            self.editors[key]["state"].export = result
            messagebox.showinfo("Pack exportado", f"{result.frames} frames\n\n{result.root}\n\n{result.archive}", parent=self)
        self._submit("Exportando pack…", lambda p, c: PackExporter.export(folder, frames, settings, p, c), done)

    def export_animation_gif(self, key):
        if self.editors[key]["state"].dirty and not self.busy:
            self.convert(key, on_done=lambda: self.export_animation_gif(key))
            return
        if not self._ready(key):
            return
        try:
            settings = self._animation_settings(key)
            frames = tuple(self.editors[key]["state"].frames)
            order = settings.sequence(len(frames))
        except ValueError as error:
            self._error(str(error))
            return
        selected = filedialog.asksaveasfilename(parent=self, title="Exportar GIF editado", defaultextension=".gif", filetypes=[("GIF animado", "*.gif")])
        if selected:
            self._submit("Exportando GIF…", lambda p, c: export_gif(selected, frames, settings.fps, order, c),
                         lambda path: messagebox.showinfo("GIF exportado", str(path), parent=self))

    def validate_pack(self, archive=False):
        if self.busy:
            return
        selected = filedialog.askopenfilename(parent=self, title="Validar ZIP", filetypes=[("ZIP", "*.zip")]) if archive else filedialog.askdirectory(parent=self, title="Validar carpeta de assets")
        if not selected:
            return
        from .validation import validate_path

        def done(report):
            window = ctk.CTkToplevel(self)
            window.title("Validación de assets")
            window.geometry("720x480")
            box = ctk.CTkTextbox(window, font=("Consolas", 12))
            box.pack(fill="both", expand=True, padx=16, pady=16)
            box.insert("1.0", report.to_text())
            box.configure(state="disabled")
            window.after(50, window.lift)
        self._submit("Validando pack…", lambda p, c: validate_path(selected, cancel=c), done)

    def push_export(self, key):
        result = self.editors[key]["state"].export
        if not result:
            self._error("Exporta el pack primero.")
            return
        if result.remote_parent != "/ext/asset_packs":
            self._error("Dolphin oficial requiere combinar manifest.txt con el existente. Esta base exporta ese formato para instalación manual.")
            return
        self._push(result.root, result.remote_parent)

    def _build_usb(self):
        page = ctk.CTkFrame(self, fg_color=PANEL)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(3, weight=1)
        self.pages["usb"] = page
        toolbar = ctk.CTkFrame(page, fg_color="transparent")
        toolbar.grid(row=0, column=0, padx=16, pady=16, sticky="ew")
        toolbar.grid_columnconfigure(0, weight=1)
        self.port_value = tk.StringVar(value="Sin puertos")
        self.port_menu = ctk.CTkOptionMenu(toolbar, variable=self.port_value, values=["Sin puertos"], fg_color="#30333B")
        self.port_menu.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._button(toolbar, "Detectar", self.scan_ports, width=85).grid(row=0, column=1, padx=4)
        self._button(toolbar, "Conectar", self.connect_usb, True, width=90).grid(row=0, column=2, padx=4)
        self._button(toolbar, "Desconectar", self.disconnect_usb, width=105).grid(row=0, column=3, padx=4)
        self.device_label = ctk.CTkLabel(page, text="Cierra qFlipper antes de conectar. Se validará la identidad del dispositivo.", text_color=MUTED, anchor="w")
        self.device_label.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="ew")
        location = ctk.CTkFrame(page, fg_color="transparent")
        location.grid(row=2, column=0, padx=16, sticky="ew")
        location.grid_columnconfigure(1, weight=1)
        self._button(location, "↑", self.parent_remote, width=38).grid(row=0, column=0, padx=(0, 8))
        self.remote_path = tk.StringVar(value="/ext")
        entry = ctk.CTkEntry(location, textvariable=self.remote_path, height=36)
        entry.grid(row=0, column=1, sticky="ew")
        entry.bind("<Return>", lambda _: self.refresh_remote())
        self._button(location, "Explorar", self.refresh_remote, width=90).grid(row=0, column=2, padx=(8, 0))

        tree_frame = ctk.CTkFrame(page, fg_color=PANEL)
        tree_frame.grid(row=3, column=0, padx=16, pady=16, sticky="nsew")
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Flipper.Treeview", background="#22252B", foreground="#EEEEEE", fieldbackground="#22252B", rowheight=32, borderwidth=0)
        style.configure("Flipper.Treeview.Heading", background="#30333B", foreground="#EDEDED", relief="flat")
        style.map("Flipper.Treeview", background=[("selected", "#744313")])
        self.tree = ttk.Treeview(tree_frame, columns=("type", "size"), show="tree headings", style="Flipper.Treeview", selectmode="browse")
        self.tree.heading("#0", text="Nombre", anchor="w")
        self.tree.heading("type", text="Tipo")
        self.tree.heading("size", text="Tamaño")
        self.tree.column("#0", width=320, stretch=True)
        self.tree.column("type", width=90, stretch=False)
        self.tree.column("size", width=100, stretch=False, anchor="e")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", self.open_remote)
        self.remote_entries = {}
        self.listed_path = "/ext"
        actions = ctk.CTkFrame(page, fg_color="transparent")
        actions.grid(row=4, column=0, padx=16, pady=(0, 16), sticky="ew")
        self._button(actions, "Cargar carpeta aquí", self.push_folder, True).pack(side="left", padx=(0, 10))
        self._button(actions, "Eliminar selección", self.remove_remote).pack(side="left")
        ctk.CTkLabel(page, text="Cada carga crea una subcarpeta nueva. No reemplaza packs existentes.\nLas carpetas no vacías no se eliminan de forma recursiva.", text_color=MUTED, justify="left").grid(row=5, column=0, padx=20, pady=(0, 18), sticky="w")

    def scan_ports(self):
        def done(ports):
            self.ports = {f"{p.device} · {p.description}{' · candidato Flipper' if p.likely_flipper else ''}": p.device for p in ports}
            values = list(self.ports) or ["Sin puertos"]
            self.port_menu.configure(values=values)
            self.port_value.set(values[0])
        self._submit("Detectando puertos…", lambda p, c: self.usb.detect_ports(), done)

    def connect_usb(self):
        port = getattr(self, "ports", {}).get(self.port_value.get())
        if not port:
            self._error("No hay puerto seleccionado. Conecta el Flipper y pulsa Detectar.")
            return

        def done(identity):
            device = self.usb.device
            self.device_label.configure(text="  |  ".join(f"{name}: {device[field]}" for name, field in (("Equipo", "hardware_model"), ("Nombre", "hardware_name"), ("Firmware", "firmware_version")) if field in device)[:160])
            self.refresh_remote()
        self._submit(f"Conectando a {port}…", lambda p, c: self.usb.connect(port), done)

    def disconnect_usb(self):
        def done(_):
            self.tree.delete(*self.tree.get_children())
            self.remote_entries.clear()
            self.device_label.configure(text="USB desconectado.")
        self._submit("Desconectando…", lambda p, c: self.usb.disconnect(), done)

    def refresh_remote(self):
        path = self.remote_path.get()

        def done(entries):
            self.tree.delete(*self.tree.get_children())
            self.remote_entries.clear()
            self.listed_path = self.usb.path(path)
            for index, item in enumerate(entries):
                key = str(index)
                self.remote_entries[key] = item
                self.tree.insert("", "end", iid=key, text=item.name, values=("Carpeta" if item.directory else "Archivo", "—" if item.directory else f"{item.size:,} B"))
        self._submit(f"Explorando {path}…", lambda p, c: self.usb.list_directory(path), done)

    def parent_remote(self):
        self.remote_path.set(str(PurePosixPath(self.remote_path.get()).parent))
        self.refresh_remote()

    def open_remote(self, _event=None):
        selection = self.tree.selection()
        if selection and self.remote_entries[selection[0]].directory and not self.busy:
            self.remote_path.set(f"{self.listed_path.rstrip('/')}/{self.remote_entries[selection[0]].name}")
            self.refresh_remote()

    def remove_remote(self):
        selection = self.tree.selection()
        if not selection or self.busy:
            return
        path = f"{self.listed_path.rstrip('/')}/{self.remote_entries[selection[0]].name}"
        if messagebox.askyesno("Eliminar de la SD", f"¿Eliminar este elemento?\n\n{path}\n\nSolo se pueden borrar carpetas vacías.", parent=self):
            self._submit("Eliminando…", lambda p, c: self.usb.remove(path), lambda _: self.refresh_remote())

    def push_folder(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(parent=self, title="Seleccionar carpeta para cargar")
        if folder:
            self._push(Path(folder), self.remote_path.get())

    def _push(self, folder, parent):
        if self.busy:
            return
        if not self.usb.connected:
            self.show_page("usb")
            self._error("Conecta el Flipper desde el Gestor USB antes de cargar el pack.")
            return
        if messagebox.askyesno("Cargar por USB", f"Origen:\n{folder}\n\nDestino nuevo:\n{parent}/{folder.name}\n\n¿Iniciar carga?", parent=self):
            self._submit("Cargando por USB…", lambda p, c: self.usb.upload_directory(folder, parent, p, c),
                         lambda result: messagebox.showinfo("Carga verificada", f"Archivos verificados con MD5.\n\n{result}", parent=self))

    def _submit(self, label, work, done=None):
        if self.busy or self.closing:
            return False
        for key in self.editors:
            self._stop_playback(key)
        self.busy = True
        self.cancel.clear()
        self.status.configure(text=label)
        self.progress.set(0)
        self.cancel_button.configure(state="normal")

        def worker():
            def progress(fraction, message):
                self.events.put(("progress", (fraction, message)))
            try:
                result = work(progress, self.cancel)
                self.events.put(("done", (done, result)))
            except InterruptedError as error:
                self.events.put(("cancelled", str(error)))
            except Exception as error:
                # Expected connection/validation failures belong in the UI.
                # Keep unexpected programming errors visible during development.
                if not isinstance(error, (ValueError, OSError, RuntimeError)):
                    traceback.print_exc()
                self.events.put(("error", str(error)))
        threading.Thread(target=worker, name="flipper-worker", daemon=True).start()
        return True

    def _drain(self):
        try:
            for _ in range(80):
                kind, data = self.events.get_nowait()
                if kind == "preview":
                    key, revision, index, preview, error = data
                    self.preview_running.discard(key)
                    state = self.editors[key]["state"]
                    if state.revision == revision and state.index == index:
                        if error:
                            self.status.configure(text=error[:160])
                        else:
                            state.preview, state.preview_index = preview, index
                            if not self.closing and "stroke" not in self.editors[key]:
                                self.render(key)
                    elif not self.closing:
                        self._schedule_preview(key)
                elif kind == "progress":
                    fraction, message = data
                    self.progress.set(max(0, min(1, fraction)))
                    self.status.configure(text=message)
                else:
                    self.busy = False
                    self.cancel_button.configure(state="disabled")
                    self.connection.configure(text=f"USB · {self.usb.port}" if self.usb.connected else "USB · Desconectado", text_color=ORANGE if self.usb.connected else MUTED)
                    if kind == "done":
                        self.progress.set(1)
                        self.status.configure(text="Operación completada.")
                        callback, result = data
                        if callback and not self.closing:
                            try:
                                callback(result)
                            except Exception as error:
                                self._error(str(error))
                    elif kind == "cancelled":
                        self.status.configure(text=data)
                        self.progress.set(0)
                    else:
                        self.status.configure(text=f"Error: {data}"[:160])
                        self.progress.set(0)
                        if not self.closing:
                            self._error(data)
        except queue.Empty:
            pass
        if self.closing and not self.busy:
            self.preview_pool.shutdown(wait=False, cancel_futures=True)
            self.usb.disconnect()
            self.destroy()
        else:
            self.after(50, self._drain)

    def _error(self, message):
        messagebox.showerror("Flipper Asset Maker", message, parent=self)

    def _close(self):
        if self.closing:
            return
        self.closing = True
        self.cancel.set()
        for key in self.editors:
            self._stop_playback(key)
        for timer in self.preview_timers.values():
            self.after_cancel(timer)
        self.preview_timers.clear()
        self.status.configure(text="Cerrando; esperando que termine el bloque de trabajo actual…")
        # _drain waits without blocking Tk, then releases serial and destroys widgets.
