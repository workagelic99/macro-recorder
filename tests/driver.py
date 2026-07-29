#!/usr/bin/env python3
"""
Self-driving proof harness for macro.py.

Every proof runs macro.py as its OWN subprocess under a hard watchdog with a
kill -9 fallback, and synthesizes the test input from here. Nothing is
simulated: real events go through the OS and the results are read back off
disk or off a live listener.

Safety rules baked in:
  - all test clicks land inside the borderless window from safe_target.py
  - the only keys used are F13, F14, F15, which macOS does not bind to
    anything by default
  - no proof runs unless the safe target window actually came up

Usage:
  driver.py probe        two second permission smoke probe
  driver.py roundtrip    record a known sequence, replay it, compare
  driver.py interrupt    stop hotkey halts an infinite loop
  driver.py autoclick    autoclicker delays are random inside the bounds
"""

import json
import signal
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

from pynput import keyboard, mouse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import macro  # noqa: E402

TOOL_DIR = Path(__file__).resolve().parent.parent
VENV_PY = TOOL_DIR / ".venv" / "bin" / "python"
MACRO = TOOL_DIR / "macro.py"
TARGET = Path(__file__).resolve().parent / "safe_target.py"

HOTKEY_RECORD = keyboard.Key.f6
HOTKEY_PLAY = keyboard.Key.f7
HOTKEY_AUTOCLICK = keyboard.Key.f8
TEST_KEYS = [keyboard.Key.f13, keyboard.Key.f14, keyboard.Key.f15]

BOOT_SECONDS = 2.5      # time to let a macro.py subprocess get its listeners up
WATCHDOG = 45           # hard ceiling on any macro.py subprocess


# --- subprocess plumbing ----------------------------------------------------

class Proc:
    """macro.py under a watchdog, with kill -9 as the fallback."""

    def __init__(self, args, label):
        self.label = label
        self.p = subprocess.Popen(
            [str(VENV_PY), str(MACRO)] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, start_new_session=True)
        self.timer = threading.Timer(WATCHDOG, self._kill9)
        self.timer.daemon = True
        self.timer.start()

    def _kill9(self):
        try:
            self.p.kill()
            print("   WATCHDOG: kill -9 fired on %s" % self.label)
        except Exception:
            pass

    def stop(self):
        self.timer.cancel()
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait(timeout=5)
        return self.p.stdout.read() if self.p.stdout else ""


def start_target():
    p = subprocess.Popen([str(VENV_PY), str(TARGET)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, start_new_session=True)
    line = p.stdout.readline()
    try:
        rect = json.loads(line)
    except Exception:
        p.kill()
        raise SystemExit("safe target window did not start, refusing to click. "
                         "Output: %r" % line)
    time.sleep(0.6)  # let it actually paint
    return p, rect


def safe_points(rect):
    """Three click points comfortably inside the target window."""
    cx = rect["left"] + rect["width"] // 2
    cy = rect["top"] + rect["height"] // 2
    return [(cx - 150, cy - 80), (cx + 150, cy - 80), (cx, cy + 120)]


# --- capture ----------------------------------------------------------------

class Capture:
    """Listens and timestamps what actually happened on the machine."""

    def __init__(self):
        self.events = []
        self.lock = threading.Lock()
        self.t0 = None
        self.km = keyboard.Listener(on_press=self._key)
        self.mm = mouse.Listener(on_click=self._click)

    def start(self):
        macro.warm_up_pyobjc()   # same pyobjc lazy-import race as the GUI
        self.km.start()
        self.mm.start()
        self.km.wait()
        self.mm.wait()
        time.sleep(0.4)

    def stop(self):
        self.km.stop()
        self.mm.stop()

    def _stamp(self, ev):
        now = time.perf_counter()
        with self.lock:
            if self.t0 is None:
                self.t0 = now
            ev["t"] = now - self.t0
            self.events.append(ev)

    def _key(self, key):
        # our own hotkey synthesis is not part of the payload under test
        if key in (HOTKEY_RECORD, HOTKEY_PLAY, HOTKEY_AUTOCLICK):
            return
        name = key.name if isinstance(key, keyboard.Key) else str(key)
        self._stamp({"type": "key", "key": name})

    def _click(self, x, y, button, pressed):
        if not pressed:
            return
        self._stamp({"type": "click", "x": int(x), "y": int(y),
                     "button": button.name})

    def snapshot(self):
        with self.lock:
            return list(self.events)


# --- synthesis --------------------------------------------------------------

kbd = keyboard.Controller()
ms = mouse.Controller()


def tap(key):
    kbd.press(key)
    kbd.release(key)


def synth_sequence(points):
    """A known click-and-key sequence with deliberate gaps. Returns the plan."""
    plan = []
    steps = [
        ("click", points[0], 0.30),
        ("key", TEST_KEYS[0], 0.25),
        ("click", points[1], 0.40),
        ("key", TEST_KEYS[1], 0.20),
        ("click", points[2], 0.35),
    ]
    for kind, payload, gap in steps:
        if kind == "click":
            ms.position = payload
            time.sleep(0.05)
            ms.click(mouse.Button.left)
            plan.append({"type": "click", "x": payload[0], "y": payload[1]})
        else:
            tap(payload)
            plan.append({"type": "key", "key": payload.name})
        time.sleep(gap)
    return plan


# --- proofs -----------------------------------------------------------------

def proof_probe():
    """Two second capture that must see a synthesized event."""
    print("PROBE: 2 second listener capture with a synthesized F13")
    cap = Capture()
    cap.start()
    deadline = time.perf_counter() + 2.0
    tap(TEST_KEYS[0])
    while time.perf_counter() < deadline:
        time.sleep(0.05)
    cap.stop()
    seen = cap.snapshot()
    print("   events seen: %d -> %s" % (len(seen), seen))
    ok = any(e.get("key") == "f13" for e in seen)
    print("   RESULT: %s" % ("PASS, both permissions are live" if ok else
                             "FAIL, listener saw nothing. Permissions are NOT "
                             "live, or Terminal was not restarted."))
    return ok


def proof_roundtrip():
    print("ROUNDTRIP + TIMING")
    target, rect = start_target()
    pts = safe_points(rect)
    print("   safe target window at %s, click points %s" % (rect, pts))
    try:
        # --- record leg
        rec = Proc(["record", "proof_seq"], "record")
        time.sleep(BOOT_SECONDS)
        tap(HOTKEY_RECORD)
        time.sleep(0.4)
        plan = synth_sequence(pts)
        time.sleep(0.3)
        tap(HOTKEY_RECORD)
        time.sleep(0.8)
        rec_out = rec.stop()

        path = TOOL_DIR / "recordings" / "proof_seq.json"
        if not path.exists():
            print("   FAIL: no recording written. macro.py said:\n%s" % rec_out)
            return False
        data = json.loads(path.read_text())
        recorded = data["events"]
        rec_clicks = [e for e in recorded
                      if e["type"] == "click" and e["pressed"]]
        rec_keys = [e for e in recorded
                    if e["type"] == "key" and e["pressed"]]
        print("   recorded %d raw events: %d click-presses, %d key-presses"
              % (len(recorded), len(rec_clicks), len(rec_keys)))

        plan_clicks = [p for p in plan if p["type"] == "click"]
        plan_keys = [p for p in plan if p["type"] == "key"]
        rec_ok = (
            len(rec_clicks) == len(plan_clicks)
            and len(rec_keys) == len(plan_keys)
            and all((c["x"], c["y"]) == (p["x"], p["y"])
                    for c, p in zip(rec_clicks, plan_clicks))
            and all(c.get("name") == p["key"]
                    for c, p in zip(rec_keys, plan_keys))
        )
        print("   recording matches what was synthesized: %s" % rec_ok)
        if not rec_ok:
            print("   synthesized: %s" % plan)
            print("   recorded:    %s" % [
                {k: v for k, v in e.items() if k != "t"} for e in recorded])

        hotkeys_leaked = [e for e in recorded
                          if e["type"] == "key" and e.get("name") in
                          ("f6", "f7", "f8")]
        print("   tool hotkeys filtered out of the recording: %s"
              % (not hotkeys_leaked))

        # --- replay leg
        cap = Capture()
        cap.start()
        play = Proc(["play", "proof_seq"], "play")
        time.sleep(BOOT_SECONDS)
        tap(HOTKEY_PLAY)
        span = recorded[-1]["t"] - recorded[0]["t"]
        time.sleep(span + 2.0)
        play_out = play.stop()
        cap.stop()
        replayed = cap.snapshot()

        rep_clicks = [e for e in replayed if e["type"] == "click"]
        rep_keys = [e for e in replayed if e["type"] == "key"]
        print("   replayed and captured: %d clicks, %d keys"
              % (len(rep_clicks), len(rep_keys)))

        pos_ok = (len(rep_clicks) == len(rec_clicks)
                  and all((a["x"], a["y"]) == (b["x"], b["y"])
                          for a, b in zip(rep_clicks, rec_clicks)))
        key_ok = (len(rep_keys) == len(rec_keys)
                  and all(a["key"] == b.get("name")
                          for a, b in zip(rep_keys, rec_keys)))
        print("   replayed click positions match recorded: %s" % pos_ok)
        print("   replayed key order matches recorded:     %s" % key_ok)

        # --- timing
        jitter = []
        press_recorded = [e for e in recorded if e.get("pressed")]
        if len(replayed) == len(press_recorded) and replayed:
            r0 = press_recorded[0]["t"]
            c0 = replayed[0]["t"]
            for cap_ev, rec_ev in zip(replayed, press_recorded):
                expected = rec_ev["t"] - r0
                actual = cap_ev["t"] - c0
                jitter.append(abs(actual - expected) * 1000.0)
        if jitter:
            print("   timing error vs recorded timestamps, %d samples:"
                  % len(jitter))
            print("      median %.2f ms   worst %.2f ms"
                  % (statistics.median(jitter), max(jitter)))
        else:
            print("   timing: not comparable, replay count %d vs recorded %d"
                  % (len(replayed), len(press_recorded)))

        if play_out.strip():
            print("   macro.py play said: %s"
                  % play_out.strip().replace("\n", " | ")[:200])
        return rec_ok and pos_ok and key_ok
    finally:
        target.kill()


def proof_interrupt():
    print("INTERRUPT: stop hotkey halts an infinite loop")
    target, rect = start_target()
    try:
        cap = Capture()
        cap.start()
        play = Proc(["play", "proof_seq", "--loop", "0"], "play-forever")
        time.sleep(BOOT_SECONDS)
        tap(HOTKEY_PLAY)
        time.sleep(4.0)
        during = len(cap.snapshot())
        t_stop = time.perf_counter()
        tap(HOTKEY_PLAY)                       # the stop
        time.sleep(1.5)
        after_stop_mark = len(cap.snapshot())
        time.sleep(2.5)                        # would keep firing if not stopped
        final = len(cap.snapshot())
        out = play.stop()
        cap.stop()

        print("   events during 4s of infinite playback: %d" % during)
        print("   events 1.5s after pressing stop:       %d" % after_stop_mark)
        print("   events a further 2.5s later:           %d" % final)
        halted = final == after_stop_mark and during > 0
        print("   RESULT: %s" % ("PASS, playback stopped and stayed stopped"
                                 if halted else "FAIL, events kept arriving"))
        if out.strip():
            print("   macro.py said: %s"
                  % out.strip().replace("\n", " | ")[:200])
        return halted
    finally:
        target.kill()


def proof_autoclick():
    lo, hi = 80, 140
    print("AUTOCLICK: delays must be random inside %d to %d ms" % (lo, hi))
    target, rect = start_target()
    try:
        ms.position = safe_points(rect)[2]      # park inside the safe window
        time.sleep(0.3)
        cap = Capture()
        cap.start()
        auto = Proc(["autoclick", "--min", str(lo), "--max", str(hi)], "autoclick")
        time.sleep(BOOT_SECONDS)
        tap(HOTKEY_AUTOCLICK)
        time.sleep(4.0)
        tap(HOTKEY_AUTOCLICK)
        time.sleep(0.8)
        out = auto.stop()
        cap.stop()

        clicks = [e for e in cap.snapshot() if e["type"] == "click"]
        deltas = [(b["t"] - a["t"]) * 1000.0
                  for a, b in zip(clicks, clicks[1:])]
        print("   clicks captured: %d" % len(clicks))
        if len(deltas) < 5:
            print("   FAIL: too few clicks to judge")
            return False
        inside = [d for d in deltas if lo - 8 <= d <= hi + 20]
        print("   delay ms: min %.1f  median %.1f  max %.1f"
              % (min(deltas), statistics.median(deltas), max(deltas)))
        print("   distinct values: %d of %d samples, stdev %.1f ms"
              % (len(set(round(d) for d in deltas)), len(deltas),
                 statistics.pstdev(deltas)))
        print("   inside bounds (allowing OS overhead): %d of %d"
              % (len(inside), len(deltas)))
        ok = len(inside) >= len(deltas) * 0.9 and statistics.pstdev(deltas) > 3
        print("   RESULT: %s" % ("PASS, random and inside the bounds" if ok
                                 else "FAIL"))
        if out.strip():
            print("   macro.py said: %s"
                  % out.strip().replace("\n", " | ")[:200])
        return ok
    finally:
        target.kill()


PROOFS = {
    "probe": proof_probe,
    "roundtrip": proof_roundtrip,
    "interrupt": proof_interrupt,
    "autoclick": proof_autoclick,
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in PROOFS:
        print(__doc__)
        return 2
    started = time.strftime("%H:%M:%S")
    print("=" * 66)
    print("%s  started %s" % (sys.argv[1].upper(), started))
    print("=" * 66)
    ok = PROOFS[sys.argv[1]]()
    print("-" * 66)
    print("%s: %s" % (sys.argv[1], "PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
