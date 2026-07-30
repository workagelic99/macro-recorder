#!/usr/bin/env python3
"""
A FRESH GUI process that reports what it loaded, then quits.

Used by config_matrix.py once per config case. It builds the REAL window with
the REAL listeners, reads the live binding map and the status line the user
would actually see, prints one JSON line, and exits. Everything is read from a
main-thread timer, never from a worker thread.

The point of running a whole separate process per case is that a cold start is
the thing under test. Loading nine configs inside one process would prove the
loader works and say nothing about whether a bad file can stop the tool from
opening at all.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CONFIG = Path(sys.argv[1])

TMP_RECORDINGS = Path(tempfile.mkdtemp(prefix="macro_probe_")) / "recordings"
TMP_RECORDINGS.mkdir(parents=True)

import macro  # noqa: E402
macro.RECORDINGS_DIR = TMP_RECORDINGS

import bindings  # noqa: E402
import gui  # noqa: E402
import mainthread  # noqa: E402
from AppKit import (NSApplication,  # noqa: E402
                    NSApplicationActivationPolicyRegular)
from Foundation import NSTimer  # noqa: E402


def main():
    tool = macro.MacroTool("all", verbose=False, config_path=CONFIG)
    tool.start_listeners()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    win = gui.MacroWindow.alloc().initWithTool_(tool)
    out = {}

    def sample():
        out["map"] = {a: bindings.format_binding(b)
                      for a, b in tool.binding_map().items()}
        out["warning"] = tool.binding_warning
        out["status_line"] = str(win.status.stringValue())
        out["row_labels"] = {a: str(f.stringValue())
                             for a, f in win.binding_fields.items()}
        out["window_visible"] = bool(win.window.isVisible())
        out["listeners"] = bool(tool.kbd_listener.running
                                and tool.mouse_listener.running)
        out["main_thread"] = mainthread.on_main()

    def finish():
        ok, _ = driver.thread_verdict()
        out["all_on_main_thread"] = ok
        print("PROBE " + json.dumps(out), flush=True)

    driver = mainthread.Steps(app, [("settle", lambda: None),
                                    ("sample", sample)], on_finish=finish)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        gui.POLL_SECONDS, win, "tick:", None, True)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.5, driver, "tick:", None, True)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
