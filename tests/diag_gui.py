#!/usr/bin/env python3
"""
Census of the REAL window: are the controls actually built, on screen, and
inside the window frame? Runs the live NSApplication, samples from a timer on
the main thread, then quits.

This is the check that was missing when the Tk version shipped: it counts
VISIBLE subviews with real frames, not merely constructed objects.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from AppKit import NSApplication, NSApplicationActivationPolicyRegular  # noqa
from Foundation import NSTimer  # noqa

import macro  # noqa: E402
import gui  # noqa: E402


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
    tool = macro.MacroTool("all", verbose=False)
    tool.start_listeners()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    c = gui.MacroWindow.alloc().initWithTool_(tool)

    def report():
        w = c.window
        print("window visible=%s title=%r frame=%s"
              % (w.isVisible(), str(w.title()), w.frame().size), flush=True)
        rows = describe(w.contentView())
        visible = [r for r in rows if not r[2] and r[5] > 0 and r[6] > 0]
        print("subviews total=%d  visible-with-size=%d" % (len(rows), len(visible)),
              flush=True)
        for d, cls, hidden, x, y, ww, hh, title in rows:
            print("   %s%-16s hidden=%-5s %4d,%-4d %3dx%-3d %s"
                  % ("  " * d, cls, hidden, x, y, ww, hh, title), flush=True)
        print("table rows=%d names=%s"
              % (c.table.numberOfRows(), [n for n, _ in c.names]), flush=True)
        print("status=%r" % str(c.status.stringValue()), flush=True)
        app.terminate_(None)

    def later():
        time.sleep(1.5)
        report()

    threading.Thread(target=later, daemon=True).start()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
