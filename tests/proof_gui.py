#!/usr/bin/env python3
"""
Proof: the window opens with all its controls actually on screen, reports live
status, and stays responsive while a recording plays on loop.

This test exists in this shape because the previous version did NOT check that
anything was drawn. It measured timer cadence and status strings, both of which
a completely blank window passes, and a blank window is exactly what shipped.
So the first thing asserted here is a census of real, visible, sized controls.

Threading, corrected 2026-07-30: the previous version ran the census, the
selection, the button handlers, the status reads and app.terminate_ from a
worker thread. All of that is AppKit and none of it was allowed off the main
thread. Every step now runs from a main-thread timer and the run ASSERTS it.
Responsiveness is measured by how late a main-thread timer fires, which is a
truer measure of a stalled UI than a worker thread's own sleep accuracy.

Control names are derived from the LIVE binding map, not hardcoded to F6, F7
and F8, so this proof still means something after the hotkeys are edited.

Safety: the planted recording is keys only (F13, F14, F15, which macOS binds
to nothing) and contains no clicks. Clicks are covered by driver.py, which
confines them to a window it owns. An injected temporary recordings directory
and config path mean your own files are never read or written.
"""

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TMP_ROOT = Path(tempfile.mkdtemp(prefix="macro_proof_gui_"))
TMP_RECORDINGS = TMP_ROOT / "recordings"
TMP_RECORDINGS.mkdir(parents=True)
TMP_CONFIG = TMP_ROOT / "config.json"

import macro  # noqa: E402
macro.RECORDINGS_DIR = TMP_RECORDINGS

import gui  # noqa: E402
import mainthread  # noqa: E402
from AppKit import (NSApplication,  # noqa: E402
                    NSApplicationActivationPolicyRegular)
from Foundation import NSIndexSet, NSTimer  # noqa: E402

NAME = "proof_gui_seq"
TICK = 0.05
PLAY_TICKS = 10          # step ticks spent sampling while playback runs


def plant():
    events, t = [], 0.0
    for name in ("f13", "f14", "f15", "f13"):
        events.append({"type": "key", "kind": "special", "name": name,
                       "pressed": True, "t": round(t, 6)})
        events.append({"type": "key", "kind": "special", "name": name,
                       "pressed": False, "t": round(t + 0.02, 6)})
        t += 0.25
    macro.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    p = macro.RECORDINGS_DIR / (NAME + ".json")
    p.write_text(json.dumps({"version": macro.FORMAT_VERSION, "name": NAME,
                             "created": "planted-by-test",
                             "screen": macro.screen_size(),
                             "capture_moves": False, "events": events},
                            indent=2), encoding="utf-8")
    return len(events)


def required_titles(tool):
    """Derived from the live bindings, so an edited hotkey does not fail this."""
    return ["Recordings", "Save as",
            "Record (%s)" % tool.binding_label("record"),
            "Stop",
            "Play (%s)" % tool.binding_label("play"),
            "Playback", "Autoclicker", "Speed", "Repeat", "Loop forever",
            "Min ms", "Max ms",
            "Start (%s)" % tool.binding_label("autoclick"),
            "Hotkeys", "Record", "Play", "Autoclick", "Set", "Cancel",
            "Reset Defaults", "Test Input"]


def all_titles(view, out=None):
    out = out if out is not None else []
    for sub in view.subviews():
        if sub.isHidden():
            continue
        f = sub.frame()
        if f.size.width > 0 and f.size.height > 0:
            for g in ("title", "stringValue"):
                try:
                    v = getattr(sub, g)()
                    if v:
                        out.append(str(v))
                        break
                except Exception:
                    pass
        all_titles(sub, out)
    return out


class Sampler:
    """
    Main-thread responsiveness probe.

    Fires every TICK seconds on the main run loop and records how LATE it was.
    If playback were blocking the main thread, this timer could not fire on
    time, which is precisely the failure a user would feel as a frozen window.
    """

    def __init__(self, controller):
        self.c = controller
        self.active = False
        self.expected = None
        self.lags = []
        self.statuses = []

    def tick_(self, timer):
        now = time.perf_counter()
        if self.expected is not None and self.active:
            self.lags.append((now - self.expected) * 1000.0)
            self.statuses.append(str(self.c.status.stringValue()))
        self.expected = now + TICK


def main():
    n = plant()
    print("planted %s with %d events" % (NAME, n), flush=True)
    print("recordings dir: %s (injected)" % TMP_RECORDINGS, flush=True)

    tool = macro.MacroTool("all", verbose=False, config_path=TMP_CONFIG)
    tool.start_listeners()
    print("listeners: kbd=%s mouse=%s"
          % (tool.kbd_listener.running, tool.mouse_listener.running), flush=True)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    tool.start_raw_tap()
    c = gui.MacroWindow.alloc().initWithTool_(tool)
    sampler = Sampler(c)
    verdict = {}
    required = required_titles(tool)

    def census():
        titles = all_titles(c.window.contentView()) + [str(c.window.title())]
        missing = [r for r in required if not any(r in t for t in titles)]
        verdict["visible"] = (c.window.isVisible() and not missing)
        print("on main thread: %s" % mainthread.on_main(), flush=True)
        print("window visible: %s" % c.window.isVisible(), flush=True)
        print("controls found on screen: %d of %d"
              % (len(required) - len(missing), len(required)), flush=True)
        if missing:
            print("MISSING: %s" % missing, flush=True)
        print("table rows: %d %s"
              % (c.table.numberOfRows(), [x for x, _ in c.names]), flush=True)

    def start_play():
        names = [x for x, _ in c.names]
        if NAME not in names:
            verdict["played"] = False
            return
        c.table.selectRowIndexes_byExtendingSelection_(
            NSIndexSet.indexSetWithIndex_(names.index(NAME)), False)
        c.forever.setState_(1)
        c.sync_loop_field()
        c.onPlay_(None)
        sampler.active = True
        print("Play pressed through the real GUI handler, on the main thread",
              flush=True)

    def stop_play():
        sampler.active = False
        c.onStop_(None)
        print("Stop pressed through the real GUI handler, on the main thread",
              flush=True)

    def settle():
        sampler.statuses.append(str(c.status.stringValue()))
        verdict["played"] = True

    steps = ([("wait", lambda: None), ("census", census),
              ("start play", start_play)]
             + [("sample", lambda: None)] * PLAY_TICKS
             + [("stop play", stop_play), ("settle", lambda: None),
                ("final status", settle)])

    def finish():
        threads_ok, line = driver.thread_verdict()
        code = summarise(sampler.lags, sampler.statuses, verdict)
        print(line, flush=True)
        verdict["code"] = 0 if (code == 0 and threads_ok) else 1

    driver = mainthread.Steps(app, steps, on_finish=finish)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        gui.POLL_SECONDS, c, "tick:", None, True)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        TICK, sampler, "tick:", None, True)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.5, driver, "tick:", None, True)
    app.run()
    return verdict.get("code", 1)


def summarise(lags, statuses, verdict):
    playing = [s for s in statuses if s.startswith("Playing")]
    print(flush=True)
    print("samples: %d" % len(lags), flush=True)
    if lags:
        print("main-thread timer lateness: median %.1f ms, worst %.1f ms"
              % (statistics.median(lags), max(lags)), flush=True)
    print("status reported %d 'Playing' samples" % len(playing), flush=True)
    for line in sorted(set(playing))[:4]:
        print("   %s" % line, flush=True)
    print("final status: %r" % (statuses[-1] if statuses else "none"),
          flush=True)
    responsive = bool(lags) and max(lags) < 500
    reported = len(playing) > 0 and any("loop" in s for s in playing)
    stopped = bool(statuses) and statuses[-1].startswith("Idle")
    print(flush=True)
    print("all controls visible on screen:            %s"
          % verdict.get("visible"), flush=True)
    print("window stayed responsive during playback:  %s" % responsive,
          flush=True)
    print("status line reported live loop progress:   %s" % reported, flush=True)
    print("returned to Idle after Stop:               %s" % stopped, flush=True)
    ok = bool(verdict.get("visible")) and responsive and reported and stopped
    print("VERDICT: %s" % ("PASS" if ok else "FAIL"), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
