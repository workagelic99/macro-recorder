#!/usr/bin/env python3
"""
Proof: the window opens with all its controls actually on screen, reports live
status, and stays responsive while a recording plays on loop.

This test exists in this shape because the previous version did NOT check that
anything was drawn. It measured timer cadence and status strings, both of which
a completely blank window passes, and a blank window is exactly what shipped.
So the first thing asserted here is a census of real, visible, sized controls.

Safety: the planted recording is keys only (F13, F14, F15, which macOS binds
to nothing) and contains no clicks. Clicks are covered by driver.py, which
confines them to a window it owns.
"""

import json
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from AppKit import NSApplication, NSApplicationActivationPolicyRegular  # noqa
from Foundation import NSIndexSet, NSTimer  # noqa

import macro  # noqa: E402
import gui  # noqa: E402

NAME = "proof_gui_seq"
TICK = 0.05
PLAY_SECONDS = 5.0

REQUIRED = ["Recordings", "Save as", "Record (F6)", "Stop", "Play (F7)",
            "Playback", "Autoclicker", "Speed", "Repeat", "Loop forever",
            "Min ms", "Max ms", "Start (F8)"]


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


def summarise(lags, statuses, verdict):
    playing = [s for s in statuses if s.startswith("Playing")]
    print(flush=True)
    print("samples: %d" % len(lags), flush=True)
    if lags:
        print("main-thread sample lag: median %.1f ms, worst %.1f ms"
              % (statistics.median(lags), max(lags)), flush=True)
    print("status reported %d 'Playing' samples" % len(playing), flush=True)
    for line in sorted(set(playing))[:4]:
        print("   %s" % line, flush=True)
    print("final status: %r" % (statuses[-1] if statuses else "none"), flush=True)
    responsive = bool(lags) and max(lags) < 500
    reported = len(playing) > 0 and any("loop" in s for s in playing)
    stopped = bool(statuses) and statuses[-1].startswith("Idle")
    print(flush=True)
    print("all controls visible on screen:            %s"
          % verdict.get("visible"), flush=True)
    print("window stayed responsive during playback:  %s" % responsive, flush=True)
    print("status line reported live loop progress:   %s" % reported, flush=True)
    print("returned to Idle after Stop:               %s" % stopped, flush=True)
    ok = bool(verdict.get("visible")) and responsive and reported and stopped
    print("VERDICT: %s" % ("PASS" if ok else "FAIL"), flush=True)
    return 0 if ok else 1


def main():
    n = plant()
    print("planted %s with %d events" % (NAME, n), flush=True)

    tool = macro.MacroTool("all", verbose=False)
    tool.start_listeners()
    print("listeners: kbd=%s mouse=%s"
          % (tool.kbd_listener.running, tool.mouse_listener.running), flush=True)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    c = gui.MacroWindow.alloc().initWithTool_(tool)

    lags, statuses, verdict = [], [], {}

    def worker():
        time.sleep(1.0)

        # 1. the window is real and its controls are on screen
        titles = all_titles(c.window.contentView()) + [str(c.window.title())]
        missing = [r for r in REQUIRED if not any(r in t for t in titles)]
        verdict["visible"] = (c.window.isVisible() and not missing)
        print("window visible: %s" % c.window.isVisible(), flush=True)
        print("controls found on screen: %d of %d"
              % (len(REQUIRED) - len(missing), len(REQUIRED)), flush=True)
        if missing:
            print("MISSING: %s" % missing, flush=True)
        print("table rows: %d %s"
              % (c.table.numberOfRows(), [x for x, _ in c.names]), flush=True)

        # 2. select the planted recording and press Play through the real handler
        names = [x for x, _ in c.names]
        if NAME not in names:
            verdict["played"] = False
            app.terminate_(None)
            return
        c.table.selectRowIndexes_byExtendingSelection_(
            NSIndexSet.indexSetWithIndex_(names.index(NAME)), False)
        c.forever.setState_(1)
        c.sync_loop_field()
        c.onPlay_(None)
        print("Play pressed through the real GUI handler", flush=True)

        # 3. sample the main thread while playback hammers the event queue
        expected = time.perf_counter() + TICK
        end = time.perf_counter() + PLAY_SECONDS
        while time.perf_counter() < end:
            time.sleep(max(0, expected - time.perf_counter()))
            now = time.perf_counter()
            lags.append((now - expected) * 1000.0)
            statuses.append(str(c.status.stringValue()))
            expected = now + TICK

        c.onStop_(None)
        print("Stop pressed through the real GUI handler", flush=True)
        time.sleep(1.0)
        statuses.append(str(c.status.stringValue()))
        verdict["played"] = True
        # summarise HERE: app.terminate_ ends the process immediately, so
        # anything after app.run() never runs.
        verdict["code"] = summarise(lags, statuses, verdict)
        app.terminate_(None)

    threading.Thread(target=worker, daemon=True).start()
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        gui.POLL_SECONDS, c, "tick:", None, True)
    app.run()
    return verdict.get("code", 1)


if __name__ == "__main__":
    sys.exit(main())
