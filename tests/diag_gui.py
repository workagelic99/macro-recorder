#!/usr/bin/env python3
"""
Census of the REAL window: are the controls actually built, on screen, and
inside the window frame? Runs the live NSApplication, samples from a timer on
the main thread, then quits.

This is the check that was missing when the Tk version shipped: it counts
VISIBLE subviews with real frames, not merely constructed objects.

Threading, corrected 2026-07-30: the previous version did all of this from a
worker thread, including terminating the app. Every AppKit call now runs from a
main-thread timer through tests/mainthread.py, and the run ASSERTS it, because
a thread rule that is only intended is not a rule.

Isolation: an injected temporary recordings directory and config path, so a
diagnostic run can never touch your own recordings or hotkeys.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TMP_ROOT = Path(tempfile.mkdtemp(prefix="macro_diag_"))
TMP_RECORDINGS = TMP_ROOT / "recordings"
TMP_RECORDINGS.mkdir(parents=True)
TMP_CONFIG = TMP_ROOT / "config.json"

import macro  # noqa: E402
macro.RECORDINGS_DIR = TMP_RECORDINGS

import gui  # noqa: E402
import mainthread  # noqa: E402
from AppKit import (NSApplication,  # noqa: E402
                    NSApplicationActivationPolicyRegular)
from Foundation import NSTimer  # noqa: E402


def describe(view, depth=0, out=None):
    out = out if out is not None else []
    for sub in view.subviews():
        f = sub.frame()
        title = ""
        for getter in ("title", "stringValue"):
            try:
                v = getattr(sub, getter)()
                if v:
                    title = str(v)[:34]
                    break
            except Exception:
                pass
        out.append((depth, sub.__class__.__name__, bool(sub.isHidden()),
                    round(f.origin.x), round(f.origin.y),
                    round(f.size.width), round(f.size.height), title))
        describe(sub, depth + 1, out)
    return out


def main():
    tool = macro.MacroTool("all", verbose=False, config_path=TMP_CONFIG)
    tool.start_listeners()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    tool.start_raw_tap()
    c = gui.MacroWindow.alloc().initWithTool_(tool)

    verdict = {}

    def census():
        w = c.window
        print("on main thread: %s" % mainthread.on_main(), flush=True)
        print("window visible=%s title=%r frame=%s"
              % (w.isVisible(), str(w.title()), w.frame().size), flush=True)
        rows = describe(w.contentView())
        visible = [r for r in rows if not r[2] and r[5] > 0 and r[6] > 0]
        print("subviews total=%d  visible-with-size=%d"
              % (len(rows), len(visible)), flush=True)
        for d, cls, hidden, x, y, ww, hh, title in rows:
            print("   %s%-16s hidden=%-5s %4d,%-4d %3dx%-3d %s"
                  % ("  " * d, cls, hidden, x, y, ww, hh, title), flush=True)
        print("table rows=%d names=%s"
              % (c.table.numberOfRows(), [n for n, _ in c.names]), flush=True)
        print("status=%r" % str(c.status.stringValue()), flush=True)
        verdict["visible"] = len(visible)

    steps = [("wait", lambda: None),
             ("wait", lambda: None),
             ("census", census)]

    def finish():
        ok, line = driver.thread_verdict()
        print(line, flush=True)
        verdict["threads_ok"] = ok

    driver = mainthread.Steps(app, steps, on_finish=finish)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        gui.POLL_SECONDS, c, "tick:", None, True)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.6, driver, "tick:", None, True)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
