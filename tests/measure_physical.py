#!/usr/bin/env python3
"""
Measurement, part 2 of 2: what Gelo's PHYSICAL keys actually deliver.

Part 1 (measure_dispatch.py) drove the hotkeys with synthesized events. That
proved the dispatch chain but nothing about hardware, because a synthesized
keyDown carries a virtual keycode directly and bypasses whatever the keyboard's
top row really emits. This file closes that gap.

It watches two layers at once for the same keypress:

  RAW    a passive, listen-only CGEventTap on keyDown, keyUp, flagsChanged and
         NSSystemDefined (type 14). Listen-only means it cannot swallow or
         alter input: there is no intercept callback and nothing is returned
         to the system.
  PYNPUT a normal pynput listener, so we see exactly what the tool's own
         callback layer receives, or does not.

For NSSystemDefined events it decodes data1 into key type, press/release
state and the repeat bit, which share bits in that one field.

It also prints its own launch context (executable, parent process chain) so
the permission context is recorded from the run itself rather than described.

Run it from the SAME place you launch the GUI. No keys are swallowed, nothing
is recorded to disk, and it exits on its own.
"""

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import Quartz  # noqa: E402
from AppKit import NSEvent  # noqa: E402
from pynput import keyboard  # noqa: E402

WINDOW = 5.0        # seconds allowed per prompted keypress

# --synthetic injects the key itself instead of waiting for fingers. That is
# ONLY for validating this harness and for recording the synthetic baseline to
# contrast against the physical run. It proves nothing about hardware, which
# is the entire point of the physical run.
SYNTHETIC = "--synthetic" in sys.argv

# --freerun captures everything continuously for a fixed period instead of
# slicing into prompted windows. Attribution is then by DECODED IDENTITY and
# order, not by which timed slot a press happened to land in, so a mistimed
# keypress cannot be recorded against the wrong step. Use this when the person
# pressing keys cannot see the prompts in real time.
FREERUN = "--freerun" in sys.argv
FREERUN_SECONDS = 90.0

# NSSystemDefined key types. Names from IOKit's NX_KEYTYPE_* constants.
NX_KEYTYPES = {
    0: "SOUND_UP", 1: "SOUND_DOWN", 2: "BRIGHTNESS_UP", 3: "BRIGHTNESS_DOWN",
    4: "CAPS_LOCK", 5: "HELP", 6: "POWER_KEY", 7: "MUTE", 8: "UP_ARROW",
    9: "DOWN_ARROW", 10: "NUM_LOCK", 11: "CONTRAST_UP", 12: "CONTRAST_DOWN",
    13: "LAUNCH_PANEL", 14: "EJECT", 15: "VIDMIRROR", 16: "PLAY",
    17: "NEXT", 18: "PREVIOUS", 19: "FAST", 20: "REWIND",
    21: "ILLUMINATION_UP", 22: "ILLUMINATION_DOWN", 23: "ILLUMINATION_TOGGLE",
}
# What the installed pynput darwin backend actually maps from NSSystemDefined.
PYNPUT_MAPPED = {16: "media_play_pause", 17: "media_next", 18: "media_previous",
                 7: "media_volume_mute", 0: "media_volume_up",
                 1: "media_volume_down", 14: "media_eject"}

STEPS = [
    ("ordinary key", "Press and release the letter A once"),
    ("bare F6", "Press and release F6 on its own, NO fn key"),
    ("bare F7", "Press and release F7 on its own, NO fn key"),
    ("bare F8", "Press and release F8 on its own, NO fn key"),
    ("fn+F6", "Hold fn (or the Globe key) and press F6"),
    ("fn+F7", "Hold fn (or the Globe key) and press F7"),
    ("fn+F8", "Hold fn (or the Globe key) and press F8"),
]

lock = threading.Lock()
raw_events = []      # (t, kind, detail)
pyn_events = []      # (t, repr)
current = {"step": None}


def launch_context():
    print("LAUNCH CONTEXT of this measurement run")
    print("  executable    : %s" % sys.executable)
    print("  script        : %s" % os.path.abspath(__file__))
    print("  cwd           : %s" % os.getcwd())
    pid = os.getpid()
    chain = []
    for _ in range(10):
        try:
            out = os.popen("ps -o ppid= -p %d 2>/dev/null" % pid).read().strip()
            if not out or out == "1":
                break
            pid = int(out)
            name = os.popen("ps -o comm= -p %d 2>/dev/null" % pid).read().strip()
            chain.append(name)
        except Exception:
            break
    print("  parent chain  : %s" % (" <- ".join(chain) if chain else "unknown"))
    fn = os.popen(
        "defaults read -g com.apple.keyboard.fnState 2>/dev/null").read().strip()
    print("  fnState       : %s" % (fn if fn else "unset (macOS default 0, "
                                    "top row acts as media/brightness keys)"))
    print()


def decode_system(data1):
    key_type = (data1 & 0xFFFF0000) >> 16
    key_flags = data1 & 0x0000FFFF
    key_state = (key_flags & 0xFF00) >> 8
    repeat = bool(key_flags & 0x1)
    state = {0x0A: "down", 0x0B: "up"}.get(key_state, "0x%02X" % key_state)
    return key_type, NX_KEYTYPES.get(key_type, "UNKNOWN_%d" % key_type), state, repeat


def tap_callback(proxy, etype, event, refcon):
    """Runs on the MAIN thread, because the tap is on the main run loop."""
    try:
        step = current["step"]
        if step is None:
            return event
        if etype == Quartz.kCGEventKeyDown or etype == Quartz.kCGEventKeyUp:
            code = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode)
            flags = Quartz.CGEventGetFlags(event)
            rep = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventAutorepeat)
            detail = ("type=%s(%d) keycode=%d flags=0x%X autorepeat=%d"
                      % ("keyDown" if etype == Quartz.kCGEventKeyDown
                         else "keyUp", etype, code, flags, rep))
            with lock:
                raw_events.append((time.time(), step, detail))
        elif etype == 14:      # NSSystemDefined
            ns = NSEvent.eventWithCGEvent_(event)
            sub = ns.subtype() if ns is not None else -1
            data1 = ns.data1() if ns is not None else 0
            flags = Quartz.CGEventGetFlags(event)
            ktype, kname, state, repeat = decode_system(data1)
            mapped = PYNPUT_MAPPED.get(ktype)
            detail = ("type=NSSystemDefined(14) subtype=%s flags=0x%X "
                      "data1=0x%08X decoded_keytype=%d(%s) state=%s repeat=%s "
                      "pynput_maps_it=%s"
                      % (sub, flags, data1, ktype, kname, state, repeat,
                         mapped if mapped else "NO"))
            with lock:
                raw_events.append((time.time(), step, detail))
        elif etype == Quartz.kCGEventFlagsChanged:
            code = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode)
            flags = Quartz.CGEventGetFlags(event)
            with lock:
                raw_events.append((time.time(), step,
                                   "type=flagsChanged keycode=%d flags=0x%X"
                                   % (code, flags)))
    except Exception as exc:
        with lock:
            raw_events.append((time.time(), current["step"],
                               "DECODE ERROR %r" % (exc,)))
    return event      # listen-only tap, always hand the event straight back


def on_press(key):
    with lock:
        pyn_events.append((time.time(), current["step"], "PRESS   %r" % (key,)))


def on_release(key):
    with lock:
        pyn_events.append((time.time(), current["step"], "release %r" % (key,)))


SYNTH_KEYS = {
    "ordinary key": keyboard.KeyCode.from_char("a"),
    "bare F6": keyboard.Key.f6,
    "bare F7": keyboard.Key.f7,
    "bare F8": keyboard.Key.f8,
    "fn+F6": keyboard.Key.f6,
    "fn+F7": keyboard.Key.f7,
    "fn+F8": keyboard.Key.f8,
}


def inject_for(step):
    key = SYNTH_KEYS.get(step)
    if key is None:
        return
    c = keyboard.Controller()
    c.press(key)
    c.release(key)


def sequencer(stop_after):
    time.sleep(1.0)
    print("=" * 78)
    print("Follow the prompts. Each has %.0f seconds. Nothing is swallowed."
          % WINDOW)
    print("=" * 78)
    for name, instruction in STEPS:
        current["step"] = name
        print()
        print(">>> %s" % instruction)
        sys.stdout.flush()
        if SYNTHETIC:
            time.sleep(0.5)
            inject_for(name)
            time.sleep(1.0)
        else:
            time.sleep(WINDOW)
        with lock:
            r = [d for _, s, d in raw_events if s == name]
            p = [d for _, s, d in pyn_events if s == name]
        print("    raw events   : %d" % len(r))
        for d in r:
            print("       %s" % d)
        print("    pynput saw   : %s" % (p if p else "NOTHING"))
        sys.stdout.flush()
    current["step"] = None
    print()
    print("=" * 78)
    print("done, closing")
    print("=" * 78)
    sys.stdout.flush()
    stop_after()


def freerun(stop_after):
    current["step"] = "freerun"
    print("=" * 78)
    print("FREE-RUN CAPTURE, %.0f seconds. Press at your own pace, with about"
          % FREERUN_SECONDS)
    print("a second of pause between each so they separate cleanly:")
    print("   1. the letter A")
    print("   2. F6 alone, no fn")
    print("   3. F7 alone, no fn")
    print("   4. F8 alone, no fn")
    print("   5. fn+F6")
    print("   6. fn+F7")
    print("   7. fn+F8")
    print("Nothing is swallowed. Nothing is written to disk.")
    print("=" * 78)
    sys.stdout.flush()
    start = time.time()
    time.sleep(FREERUN_SECONDS)
    print()
    print("#" * 78)
    print("FREE-RUN RESULT: every event, grouped by pauses of 0.6s or more")
    print("#" * 78)
    with lock:
        raw = [(t, d) for t, s_, d in raw_events if s_ == "freerun"]
        pyn = [(t, d) for t, s_, d in pyn_events if s_ == "freerun"]
    merged = sorted([(t, "RAW   ", d) for t, d in raw]
                    + [(t, "PYNPUT", d) for t, d in pyn])
    if not merged:
        print("NOTHING CAPTURED AT ALL in %.0f seconds." % FREERUN_SECONDS)
    prev = None
    group = 0
    for t, layer, d in merged:
        if prev is None or (t - prev) >= 0.6:
            group += 1
            print()
            print("--- group %d, t+%.2fs ---" % (group, t - start))
        print("   %s  %s" % (layer, d))
        prev = t
    print()
    print("Groups should read: A, then F6, F7, F8, then fn+F6, fn+F7, fn+F8.")
    print("A group whose PYNPUT line is absent, or shows a token other than")
    print("Key.f6 / Key.f7 / Key.f8, is a key the current bindings cannot see.")
    sys.stdout.flush()
    stop_after()


def main():
    launch_context()

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    listener.wait()
    print("pynput listener running: %s" % listener.running)

    mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            | Quartz.CGEventMaskBit(14))
    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,     # cannot swallow input
        mask, tap_callback, None)
    if not tap:
        print("FAILED to create the raw event tap. Accessibility is probably "
              "not granted to this launch context.")
        listener.stop()
        return 1
    print("raw listen-only tap created: yes")

    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    loop = Quartz.CFRunLoopGetCurrent()
    Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)

    def stop():
        Quartz.CFRunLoopStop(loop)

    target = freerun if FREERUN else sequencer
    threading.Thread(target=target, args=(stop,), daemon=True).start()
    Quartz.CFRunLoopRun()
    listener.stop()
    if FREERUN:
        return 0

    print()
    print("#" * 78)
    print("SUMMARY TABLE: physical key -> what each layer delivered")
    print("#" * 78)
    for name, _ in STEPS:
        with lock:
            r = [d for _, s, d in raw_events if s == name]
            p = [d for _, s, d in pyn_events if s == name]
        print()
        print("%s" % name.upper())
        if not r:
            print("   RAW    : nothing reached the tap at all")
        for d in r:
            print("   RAW    : %s" % d)
        print("   PYNPUT : %s" % (", ".join(p) if p else "NOTHING DELIVERED"))
    print()
    print("Reminder: the tool matches on pynput tokens. Any row where PYNPUT")
    print("says NOTHING DELIVERED, or delivers a token that is not Key.f6,")
    print("Key.f7 or Key.f8, is a key the current bindings cannot ever see.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
