#!/usr/bin/env python3
"""
mainthread.py - run proof steps on the main thread, and PROVE they ran there.

AppKit is not thread safe. A view read from a worker thread usually appears to
work, which is worse than failing, because it means a proof can be green and
the same code can deadlock or draw garbage in front of the user. The earlier
versions of proof_gui.py and diag_gui.py inspected views, invoked handlers and
terminated the app from a worker thread, so they were breaking the rule they
existed to defend.

This driver fixes that shape once for every proof that needs it: steps run from
an NSTimer on the main run loop, and each step is CHECKED against
NSThread.isMainThread rather than assumed. Worker threads keep only the jobs
that are not AppKit calls, which in this project means injecting key events.
"""

import sys

from Foundation import NSThread


def on_main():
    """Are we on the main thread right now? Checked, never assumed."""
    return bool(NSThread.isMainThread())


class Steps:
    """
    A list of (label, callable) run one per timer tick on the main thread.

    Any step that somehow runs off the main thread is recorded by label rather
    than ignored, so the proof can fail loudly instead of quietly passing.
    """

    def __init__(self, app, steps, on_finish=None):
        self.app = app
        self.steps = list(steps)
        self.index = 0
        self.finished = False
        self.off_main = []
        self.raised = []
        self.on_finish = on_finish

    def tick_(self, timer):
        if self.index >= len(self.steps):
            if not self.finished:
                self.finished = True
                if self.on_finish is not None:
                    try:
                        self.on_finish()
                    except Exception as exc:
                        print("FINISH RAISED: %r" % (exc,), flush=True)
                self.app.terminate_(None)
            return
        label, fn = self.steps[self.index]
        self.index += 1
        if not on_main():
            self.off_main.append(label)
        try:
            fn()
        except Exception as exc:
            self.raised.append((label, repr(exc)))
            print("STEP RAISED: %s -> %r" % (label, exc), flush=True)

    def thread_verdict(self):
        """(ok, line) for the report."""
        ok = not self.off_main
        return ok, ("every AppKit step ran on the main thread: %s%s"
                    % (ok, "" if ok else "  OFF MAIN: %s" % self.off_main))


def report_and_exit(ok):
    sys.stdout.flush()
    return 0 if ok else 1
