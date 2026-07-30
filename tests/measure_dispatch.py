#!/usr/bin/env python3
"""
Measurement, part 1 of 2: the DISPATCH chain, synthetic input.

Builds MacroTool exactly the way gui.run does (mode "all", verbose=False) and
drives the configured hotkeys with synthesized events, then reports the whole
chain per action: event seen, callback fired, binding matched, action
requested, REQUIRED CONTEXT PRESENT, state before and after, visible result.

Synthetic injection is already proven to reach the tap on this machine, so
this part isolates everything DOWNSTREAM of delivery. It deliberately cannot
tell you whether Gelo's physical function row produces a callback at all.
That is part 2, and it needs his fingers.

Nothing here touches AppKit, so it is safe to run off the main thread.
No fix is proposed from this file alone.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import macro  # noqa: E402
from pynput import keyboard  # noqa: E402

ACTIONS = [
    ("Record", macro.HOTKEY_RECORD, "recording"),
    ("Play", macro.HOTKEY_PLAY, "playing"),
    ("Autoclick", macro.HOTKEY_AUTOCLICK, "autoclicking"),
]


def main():
    print("=" * 78)
    print("DISPATCH MEASUREMENT (synthetic input, fresh MacroTool per action)")
    print("built exactly as gui.run does: MacroTool('all', verbose=False)")
    print("=" * 78)

    kbd = keyboard.Controller()
    rows = []

    for label, hotkey, busy_state in ACTIONS:
        # A FRESH tool per action, never primed by a button or a prior action.
        tool = macro.MacroTool("all", verbose=False)

        seen = {"callbacks": 0, "hotkey_callbacks": 0}

        # The spy must take EXACTLY one parameter. pynput 1.8.2 inspects the
        # callback signature and passes (key, injected) to anything that
        # accepts two, so a spy with extra keyword parameters silently
        # receives the injected bool in the second slot and blows up before
        # reaching the real handler. That produced a completely void first
        # measurement run.
        def make_spy(real, counters, hk):
            def spy(key):
                counters["callbacks"] += 1
                if key == hk:
                    counters["hotkey_callbacks"] += 1
                return real(key)
            return spy

        tool.on_key_press = make_spy(tool.on_key_press, seen, hotkey)
        tool.start_listeners()

        running = (tool.kbd_listener.running, tool.mouse_listener.running)
        state_before = tool.state
        name_before = tool.name
        msg_before = tool.last_message

        kbd.press(hotkey)
        kbd.release(hotkey)
        time.sleep(0.9)

        state_after = tool.state
        msg_after = tool.last_message
        transitioned = state_after == busy_state

        # required context, as the engine would need it
        if label == "Record":
            ctx = "name=%r valid=%s" % (tool.name, macro.valid_name(tool.name))
        elif label == "Play":
            ctx = "name=%r" % (tool.name,)
        else:
            ctx = "min=%r max=%r" % (tool.click_min_ms, tool.click_max_ms)

        rows.append({
            "action": label,
            "key": macro.key_label(hotkey),
            "listener_running": running,
            "callbacks": seen["callbacks"],
            "hotkey_callbacks": seen["hotkey_callbacks"],
            "binding_matched": hotkey in macro.HOTKEYS,
            "context": ctx,
            "state_before": state_before,
            "state_after": state_after,
            "transitioned": transitioned,
            "message": msg_after if msg_after != msg_before else "(none)",
            "name_at_dispatch": name_before,
        })

        tool.request_stop()
        time.sleep(0.3)
        tool.stop_listeners()
        time.sleep(0.2)

    print()
    for r in rows:
        print("-" * 78)
        print("ACTION %s   physical token synthesized: %s"
              % (r["action"], r["key"]))
        print("  listener running (kbd, mouse) : %s" % (r["listener_running"],))
        print("  callbacks fired               : %d (of which this hotkey: %d)"
              % (r["callbacks"], r["hotkey_callbacks"]))
        print("  binding matched in HOTKEYS    : %s" % r["binding_matched"])
        print("  tool.name at dispatch         : %r" % r["name_at_dispatch"])
        print("  REQUIRED CONTEXT              : %s" % r["context"])
        print("  state before -> after         : %s -> %s"
              % (r["state_before"], r["state_after"]))
        print("  ACTION TRANSITION COMPLETED   : %s" % r["transitioned"])
        print("  visible result to the user    : %s" % r["message"])
    print("-" * 78)

    print()
    print("SUMMARY, dispatch layer only:")
    for r in rows:
        print("  %-10s callback=%s  matched=%s  context_ok=%s  transitioned=%s"
              % (r["action"], r["hotkey_callbacks"] > 0, r["binding_matched"],
                 "see row", r["transitioned"]))
    print()
    print("NOTE: verbose=False in gui.run, so tool.say() prints NOTHING. Any")
    print("message above was invisible to Gelo in the real app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
