"""Editable numbers with a text draft separate from the last valid numeric value.

Deleting text, typing a sign or a decimal separator must never write invalid
strings into Tk IntVar/DoubleVar or into CustomTkinter's slider callbacks.
"""

import math
import tkinter as tk
import customtkinter as ctk


class NumericField(ctk.CTkFrame):
    def __init__(self, parent, label, value, minimum, maximum, step=1):
        super().__init__(parent, fg_color="transparent")
        self.minimum, self.maximum = minimum, maximum
        self.integer = step >= 1
        self._syncing = False
        self.value = tk.IntVar(master=self, value=value) if self.integer else tk.DoubleVar(master=self, value=value)
        self.draft = tk.StringVar(master=self, value=self._format(value))
        self.value.numeric_field = self
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=label, text_color="#A0A4AE").grid(row=0, column=0, sticky="w")
        self.entry = ctk.CTkEntry(self, textvariable=self.draft, width=68, height=28)
        self.entry.grid(row=0, column=1)
        self._border = self.entry.cget("border_color")
        # No variable binding: the slider only receives valid numbers via set().
        self.slider = ctk.CTkSlider(self, from_=minimum, to=maximum,
                                   number_of_steps=round((maximum-minimum)/step),
                                   progress_color="#FF8C00", command=self._slider_changed)
        self.slider.grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
        self.slider.set(value)
        self.draft.trace_add("write", self._draft_changed)
        self.value.trace_add("write", self._value_changed)
        self.entry.bind("<FocusOut>", lambda _: self.commit())
        self.entry.bind("<Return>", lambda _: self.commit())
        self.entry.bind("<Escape>", lambda _: self.restore())

    def _format(self, number):
        return str(int(number)) if self.integer else f"{number:.12g}"

    def _parse(self):
        text = self.draft.get().strip().replace(",", ".")
        number = float(text)
        if not math.isfinite(number) or not self.minimum <= number <= self.maximum:
            raise ValueError("Valor fuera de rango.")
        if self.integer:
            if not number.is_integer():
                raise ValueError("Se requiere un entero.")
            return int(number)
        return number

    def _draft_changed(self, *_):
        if self._syncing:
            return
        try:
            number = self._parse()
        except ValueError:
            self.entry.configure(border_color="#FF8C00")
            return
        self.entry.configure(border_color=self._border)
        self._syncing = True
        try:
            self.value.set(number)
            self.slider.set(number)
        finally:
            self._syncing = False

    def _value_changed(self, *_):
        if self._syncing:
            return
        number = self.value.get()
        self._syncing = True
        try:
            self.draft.set(self._format(number))
            self.slider.set(number)
            self.entry.configure(border_color=self._border)
        finally:
            self._syncing = False

    def _slider_changed(self, number):
        self.value.set(round(number) if self.integer else number)

    def restore(self):
        self._value_changed()

    def commit(self):
        try:
            number = self._parse()
        except ValueError:
            self.restore()
            return False
        if self.value.get() != number:
            self.value.set(number)
        else:
            self._value_changed()
        return True
