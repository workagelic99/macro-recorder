#!/usr/bin/env python3
"""
gui.py - the window for macro.py.

This is a front end only. Every action calls straight into MacroTool from
macro.py, the same object the command line uses. There is no second copy of
the record or replay logic in here.

Two rules keep the window from freezing:
  1. Recording, playback and autoclicking already run on their own threads
     inside MacroTool, so no button handler ever blocks.
  2. Nothing in a worker thread ever touches a Tk widget. The window polls
     tool.status() on a timer instead, which is safe to read from anywhere.
"""

import tkinter as tk
from tkinter import ttk

import macro

POLL_MS = 150


class MacroWindow:
    def __init__(self, root, tool):
        self.root = root
        self.tool = tool
        self.last_state = None

        root.title("Macro Recorder")
        root.geometry("560x430")
        root.minsize(520, 400)

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)

        # --- recordings list
        ttk.Label(outer, text="Recordings").pack(anchor="w")
        list_row = ttk.Frame(outer)
        list_row.pack(fill="both", expand=True, pady=(2, 8))
        self.listbox = tk.Listbox(list_row, height=7, exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_row, orient="vertical",
                               command=self.listbox.yview)
        scroll.pack(side="left", fill="y")
        self.listbox.config(yscrollcommand=scroll.set)

        # --- new recording name plus the three main buttons
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="Save as").pack(side="left")
        self.name_var = tk.StringVar(value="my_sequence")
        ttk.Entry(row, textvariable=self.name_var, width=18).pack(
            side="left", padx=(6, 12))
        self.btn_record = ttk.Button(row, text="Record (F6)",
                                     command=self.on_record)
        self.btn_record.pack(side="left", padx=2)
        self.btn_stop = ttk.Button(row, text="Stop", command=self.on_stop)
        self.btn_stop.pack(side="left", padx=2)
        self.btn_play = ttk.Button(row, text="Play (F7)", command=self.on_play)
        self.btn_play.pack(side="left", padx=2)

        # --- playback options
        opts = ttk.LabelFrame(outer, text="Playback", padding=8)
        opts.pack(fill="x", pady=(0, 8))
        ttk.Label(opts, text="Speed").pack(side="left")
        self.speed_var = tk.StringVar(value="1.0")
        ttk.Entry(opts, textvariable=self.speed_var, width=6).pack(
            side="left", padx=(6, 16))
        ttk.Label(opts, text="Repeat").pack(side="left")
        self.loop_var = tk.StringVar(value="1")
        self.loop_entry = ttk.Entry(opts, textvariable=self.loop_var, width=6)
        self.loop_entry.pack(side="left", padx=(6, 16))
        self.forever_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Loop forever", variable=self.forever_var,
                        command=self.on_forever_toggle).pack(side="left")

        # --- autoclicker
        auto = ttk.LabelFrame(outer, text="Autoclicker", padding=8)
        auto.pack(fill="x", pady=(0, 8))
        ttk.Label(auto, text="Min ms").pack(side="left")
        self.min_var = tk.StringVar(value="80")
        ttk.Entry(auto, textvariable=self.min_var, width=6).pack(
            side="left", padx=(6, 12))
        ttk.Label(auto, text="Max ms").pack(side="left")
        self.max_var = tk.StringVar(value="140")
        ttk.Entry(auto, textvariable=self.max_var, width=6).pack(
            side="left", padx=(6, 12))
        self.btn_auto = ttk.Button(auto, text="Start (F8)",
                                   command=self.on_autoclick)
        self.btn_auto.pack(side="left")

        # --- status line
        self.status_var = tk.StringVar(value="Idle")
        status = ttk.Label(outer, textvariable=self.status_var,
                           relief="sunken", anchor="w", padding=6)
        status.pack(fill="x", side="bottom")

        self.refresh_list()
        self.on_forever_toggle()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(POLL_MS, self.tick)

    # --- helpers

    def refresh_list(self):
        selected = self.selected_name()
        self.listbox.delete(0, tk.END)
        self.names = []
        for name, count, created in macro.list_recordings():
            self.names.append(name)
            shown = "%s  (%s events)" % (name, count if count >= 0 else "?")
            self.listbox.insert(tk.END, shown)
        if selected in self.names:
            index = self.names.index(selected)
            self.listbox.selection_set(index)

    def selected_name(self):
        sel = self.listbox.curselection()
        if not sel or not getattr(self, "names", None):
            return None
        return self.names[sel[0]]

    def flash(self, message):
        self.tool.last_message = message

    # --- button handlers, none of these block

    def on_record(self):
        name = self.name_var.get().strip()
        if not macro.valid_name(name):
            self.flash("That name is not allowed. Letters, numbers, dot, "
                       "dash and underscore only.")
            return
        self.tool.start_record(name)

    def on_stop(self):
        if self.tool.state == "recording":
            self.tool.stop_record()
            self.refresh_list()
        else:
            self.tool.request_stop()

    def on_play(self):
        name = self.selected_name()
        if not name:
            self.flash("Pick a recording from the list first.")
            return
        try:
            speed = float(self.speed_var.get())
        except ValueError:
            self.flash("Speed must be a number, for example 1.5")
            return
        if speed <= 0:
            self.flash("Speed must be greater than 0.")
            return
        if self.forever_var.get():
            loops = 0
        else:
            try:
                loops = int(self.loop_var.get())
            except ValueError:
                self.flash("Repeat must be a whole number.")
                return
            if loops < 1:
                self.flash("Repeat must be 1 or more, or tick Loop forever.")
                return
        self.tool.start_play(name=name, speed=speed, loops=loops)

    def on_autoclick(self):
        if self.tool.state == "autoclicking":
            self.tool.request_stop()
            return
        try:
            lo = int(self.min_var.get())
            hi = int(self.max_var.get())
        except ValueError:
            self.flash("Autoclicker min and max must be whole numbers of ms.")
            return
        self.tool.start_autoclick(min_ms=lo, max_ms=hi)

    def on_forever_toggle(self):
        state = "disabled" if self.forever_var.get() else "normal"
        self.loop_entry.config(state=state)

    def on_close(self):
        self.tool.shutdown()
        self.root.destroy()

    # --- the poll loop, the only thing that touches widgets

    def tick(self):
        state = self.tool.state
        self.status_var.set(self.tool.status())

        busy = state != "idle"
        self.btn_record.config(
            state="disabled" if busy else "normal")
        self.btn_play.config(
            state="disabled" if busy else "normal")
        self.btn_stop.config(
            state="normal" if busy else "disabled")
        self.btn_auto.config(
            text="Stop (F8)" if state == "autoclicking" else "Start (F8)",
            state="normal" if state in ("idle", "autoclicking") else "disabled")

        # a recording just finished, whether by button or by hotkey
        if self.last_state == "recording" and state != "recording":
            self.refresh_list()
        self.last_state = state

        self.root.after(POLL_MS, self.tick)


def run():
    # ORDERING IS LOAD BEARING: the listeners must be up before Tk creates its
    # main run loop, or the process dies with SIGABRT. See
    # MacroTool.start_listeners for the measurement behind this.
    tool = macro.MacroTool("all", verbose=False)
    tool.start_listeners()

    root = tk.Tk()
    MacroWindow(root, tool)
    try:
        root.mainloop()
    finally:
        tool.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
