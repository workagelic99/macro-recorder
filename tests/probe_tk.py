#!/usr/bin/env python3
"""
Go / no-go probe: can Tk and pynput share one process on this Mac?

Opens a real Tk window, starts a real pynput listener, then hammers the
machine from a worker thread the way playback does, while measuring how late
the Tk event loop ticks. If Tk freezes under that load the GUI cannot be built
on tkinter and we fall back to something else.

Auto-closes. Prints a verdict.
"""

import statistics
import sys
import threading
import time
import tkinter as tk

from pynput import keyboard

RUN_SECONDS = 6.0
TICK_MS = 50


def main():
    lags = []
    seen = []
    stop = threading.Event()

    # ORDERING IS LOAD BEARING ON macOS. pynput installs a CGEventTap and spins
    # a CFRunLoop; if Tk initialises the main run loop first, starting the tap
    # aborts the whole process with SIGABRT and no traceback. Measured on
    # macOS 26.4.1 with Tk 8.5: Tk-then-listener aborted 3 of 3 runs,
    # listener-then-Tk survived every time. Start listeners first, always.
    listener = keyboard.Listener(on_press=lambda k: seen.append(k))
    listener.start()

    root = tk.Tk()
    root.title("Tk + pynput probe")
    root.geometry("360x120+80+80")
    label = tk.Label(root, text="starting", font=("Helvetica", 13))
    label.pack(expand=True)

    def worker():
        """Stands in for playback: injects events off the UI thread."""
        kbd = keyboard.Controller()
        time.sleep(0.8)
        for _ in range(40):
            if stop.is_set():
                return
            kbd.press(keyboard.Key.f13)
            kbd.release(keyboard.Key.f13)
            time.sleep(0.1)

    expected = [time.perf_counter() + TICK_MS / 1000.0]

    def tick():
        now = time.perf_counter()
        lags.append((now - expected[0]) * 1000.0)
        expected[0] = now + TICK_MS / 1000.0
        label.config(text="ticks %d   injected-seen %d   worst lag %.0f ms"
                          % (len(lags), len(seen), max(lags)))
        if not stop.is_set():
            root.after(TICK_MS, tick)

    def finish():
        stop.set()
        root.after(150, root.destroy)

    threading.Thread(target=worker, daemon=True).start()
    root.after(TICK_MS, tick)
    root.after(int(RUN_SECONDS * 1000), finish)

    try:
        root.mainloop()
    except Exception as exc:
        print("Tk mainloop raised: %r" % (exc,))
        listener.stop()
        return 1
    listener.stop()

    if not lags:
        print("VERDICT: FAIL, the Tk event loop never ticked")
        return 1
    median = statistics.median(lags)
    worst = max(lags)
    expected_ticks = int(RUN_SECONDS * 1000 / TICK_MS) - 2
    print("ticks: %d (expected about %d)" % (len(lags), expected_ticks))
    print("listener saw %d key events while the window was up" % len(seen))
    print("Tk loop lag: median %.1f ms, worst %.1f ms" % (median, worst))
    ok = len(lags) >= expected_ticks * 0.8 and worst < 1000 and len(seen) > 0
    print("VERDICT: %s" % ("PASS, tkinter is usable for the GUI" if ok
                           else "FAIL, tkinter is not usable, fall back"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
