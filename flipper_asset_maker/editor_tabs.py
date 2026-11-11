"""Keep the selected tab visible during fast keyboard-driven tab changes."""

import customtkinter as ctk


class EditorTabs(ctk.CTkTabview):
    def _grid_forget_all_tabs(self, exclude_name=None):
        # CTk 5.2 schedules cleanup 100 ms after selecting a tab. A callback
        # from an older selection must not hide a newer, already selected tab.
        if exclude_name is not None:
            exclude_name = self.get()
        super()._grid_forget_all_tabs(exclude_name=exclude_name)
