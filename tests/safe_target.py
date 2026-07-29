#!/usr/bin/env python3
"""
Safe click target for the proof runs.

Opens a plain borderless always-on-top window with no controls in it, prints
its top-left-origin rect as one JSON line on stdout, then sits there. Every
test click is aimed well inside this window, so a click cannot land on a real
button in a real app.

Not part of the macro tool. Only used by tests/driver.py.
"""

import json
import sys

from AppKit import (NSApplication, NSWindow, NSColor, NSScreen,
                    NSBackingStoreBuffered, NSFloatingWindowLevel,
                    NSApplicationActivationPolicyAccessory)
from Foundation import NSMakeRect

WIDTH = 700
HEIGHT = 500


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    screen = NSScreen.mainScreen().frame()
    sw, sh = int(screen.size.width), int(screen.size.height)

    left = (sw - WIDTH) // 2
    top = (sh - HEIGHT) // 2
    # AppKit uses a bottom-left origin, the rest of the world uses top-left.
    bottom = sh - top - HEIGHT

    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(left, bottom, WIDTH, HEIGHT), 0, NSBackingStoreBuffered,
        False)
    win.setBackgroundColor_(NSColor.darkGrayColor())
    win.setLevel_(NSFloatingWindowLevel)
    win.setIgnoresMouseEvents_(False)
    win.orderFrontRegardless()

    print(json.dumps({"left": left, "top": top,
                      "width": WIDTH, "height": HEIGHT,
                      "screen": [sw, sh]}), flush=True)
    app.run()


if __name__ == "__main__":
    sys.exit(main())
