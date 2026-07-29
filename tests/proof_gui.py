#!/usr/bin/env python3
"""
Proof: the GUI opens, reports live status, and does not freeze while a
recording is playing on loop.

Builds the real MacroWindow from gui.py against the real MacroTool, plants a
keys-only test recording, starts playback through the window's own Play
handler, and measures how late the Tk event loop ticks while the playback
thread is hammering the event queue. A frozen window shows up as missing
ticks and a large worst-case lag.

Safety: the planted recording contains only F13, F14 and F15, which macOS
binds to nothing, and no clicks at all. Clicks are covered separately by
driver.py, which confines them to a window it owns.

Auto-closes. Prints a verdict.
"""

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

import macro  # noqa: E402
import gui  # noqa: E402

NAME = "proof_gui_seq"
TICK_MS = 50
PLAY_SECONDS = 5.0


def plant_recording():
    """A short keys-only sequence, written straight to disk."""
    events = []
    t = 0.0
    for name in ("f13", "f14", "f15", "f13"):
        events.append({"type": "key", "kind": "special", "name": name,
                       "pressed": True, "t": round(t, 6)})
        events.append({"type": "key", "kind": "special", "name": name,
                       "pressed": False, "t": round(t + 0.02, 6)})
        t += 0.25
    macro.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    path = macro.RECORDINGS_DIR / (NAME + ".json")
    path.write_text(json.dumps({
        "version": macro.FORMAT_VERSION, "name": NAME,
        "created": "planted-by-test", "screen": macro.screen_size(),
        "capture_moves": False, "events": events}, indent=2), encoding="utf-8")
    return path, len(events)


def main():
    path, n = plant_recording()
    print("planted %s with %d events" % (path.name, n))

    lags = []
    statuses = []

    # Listeners before Tk, the ordering the whole GUI depends on.
    tool = macro.MacroTool("all", verbose=False)
    tool.start_listeners()

    root = tk.Tk()
    win = gui.MacroWindow(root, tool)
    print("window created, title %r" % root.title())

    # select the planted recording in the real listbox
    if NAME not in win.names:
        print("FAIL: planted recording not shown in the GUI list: %s"
              % win.names)
        root.destroy()
        tool.shutdown()
        return 1
    win.listbox.selection_clear(0, tk.END)
    win.listbox.selection_set(win.names.index(NAME))
    win.forever_var.set(True)
    win.on_forever_toggle()
    print("selected %r in the list, Loop forever ticked" % NAME)

    expected = [None]

    def probe():
        now = time.perf_counter()
        if expected[0] is not None:
            lags.append((now - expected[0]) * 1000.0)
        expected[0] = now + TICK_MS / 1000.0
        statuses.append(win.status_var.get())
        root.after(TICK_MS, probe)

    def start_play():
        win.on_play()                      # the real button handler
        print("Play pressed through the GUI handler")

    def stop_play():
        win.on_stop()                      # the real Stop button
        print("Stop pressed through the GUI handler")

    def finish():
        root.destroy()

    root.after(TICK_MS, probe)
    root.after(600, start_play)
    root.after(int(600 + PLAY_SECONDS * 1000), stop_play)
    root.after(int(600 + PLAY_SECONDS * 1000) + 900, finish)

    try:
        root.mainloop()
    except Exception as exc:
        print("Tk mainloop raised: %r" % (exc,))
        tool.shutdown()
        return 1
    tool.shutdown()

    playing = [s for s in statuses if s.startswith("Playing")]
    loop_lines = sorted(set(playing))
    expected_ticks = int((600 + PLAY_SECONDS * 1000 + 900) / TICK_MS) - 3

    print()
    print("Tk ticks: %d (expected about %d)" % (len(lags), expected_ticks))
    if lags:
        print("Tk loop lag while playback ran: median %.1f ms, worst %.1f ms"
              % (statistics.median(lags), max(lags)))
    print("status line reported %d 'Playing' samples" % len(playing))
    for line in loop_lines[:4]:
        print("   %s" % line)
    print("final status: %r" % (statuses[-1] if statuses else "none"))

    responsive = (len(lags) >= expected_ticks * 0.8
                  and lags and max(lags) < 1000)
    reported = len(playing) > 0 and any("loop" in s for s in playing)
    stopped = bool(statuses) and statuses[-1].startswith("Idle")
    print()
    print("window stayed responsive during playback: %s" % responsive)
    print("status line reported live loop progress:  %s" % reported)
    print("returned to Idle after Stop:              %s" % stopped)
    ok = responsive and reported and stopped
    print("VERDICT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
