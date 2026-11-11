"""Double-click launcher for the modular v2 application."""
import sys
import tkinter as tk
from tkinter import messagebox

if __name__ == "__main__":
    try:
        from flipper_asset_maker.gui import AppGUI
        AppGUI().mainloop()
    except Exception as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Flipper Asset Maker", f"{error}\n\nEjecuta python main.py para ver el diagnóstico completo.")
        root.destroy()
        sys.exit(1)
