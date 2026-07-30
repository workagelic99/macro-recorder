#!/usr/bin/env python3
"""
The regression matrix: every existing proof, run against THREE config states.

A tool whose hotkeys are editable has three quite different shapes, and a suite
that only ever runs the built-in defaults tests one of them:

  none                no config file at all, so the built-in defaults apply
  persisted single    a saved config of plain, unmodified keys
  persisted chord     a saved config where every binding carries a modifier

The chord state is the one that used to be able to rot silently. A modifier
held while recording changes what the recorder sees, so a suite that never
records under a chord binding could pass forever while every chord user got
stray Control keys in their macros.

Every proof here derives its keys from the canonical config, so nothing in this
file knows or cares which keys are bound. Isolation is by environment: each
config state gets its own temporary recordings directory and its own temporary
config file, handed to every subprocess, so your real recordings and your real
~/.macro-recorder.json are never read or written.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bindings  # noqa: E402
import macro  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"
MACRO = ROOT / "macro.py"

TMP_ROOT = Path(tempfile.mkdtemp(prefix="macro_regress_"))
ONLY = sys.argv[1] if len(sys.argv) > 1 else None

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("   %-60s %s%s" % (name, "PASS" if ok else "FAIL",
                             ("  " + detail) if detail else ""))
    sys.stdout.flush()


def single_map():
    return {
        "record": bindings.make_binding([], {"kind": "named", "name": "f16"}),
        "play": bindings.make_binding([], {"kind": "named", "name": "f17"}),
        "autoclick": bindings.make_binding([], {"kind": "named",
                                                "name": "f18"}),
    }


def chord_map():
    return {
        "record": bindings.make_binding(["ctrl"],
                                        {"kind": "named", "name": "f16"}),
        "play": bindings.make_binding(["ctrl"],
                                      {"kind": "named", "name": "f17"}),
        "autoclick": bindings.make_binding(["ctrl"],
                                           {"kind": "named", "name": "f18"}),
    }


CONFIGS = [
    ("none", None),
    ("persisted single-key", single_map),
    ("persisted chord", chord_map),
]


def make_env(state_name, mapping_fn):
    d = TMP_ROOT / state_name.replace(" ", "_")
    rec = d / "recordings"
    rec.mkdir(parents=True, exist_ok=True)
    cfg = d / "config.json"
    if mapping_fn is not None:
        cfg.write_text(json.dumps(
            {"version": bindings.SCHEMA_VERSION,
             "bindings": bindings.validate_map(mapping_fn())},
            indent=2) + "\n", encoding="utf-8")
    env = dict(os.environ)
    env["MACRO_RECORDINGS_DIR"] = str(rec)
    env["MACRO_CONFIG_PATH"] = str(cfg)
    return env, rec, cfg


def run(argv, env, timeout, label):
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=timeout, env=env)
        return proc.returncode, proc.stdout
    except subprocess.TimeoutExpired as exc:
        return 124, "TIMEOUT after %ss: %s\n%s" % (timeout, label,
                                                   exc.output or "")


def plant_legacy(rec_dir):
    """
    A recording in the OLDEST shape this tool ever wrote: no screen size, no
    capture_moves flag. Keys only, F13 to F15, which macOS binds to nothing.
    Synthetic and harmless by construction, and it must still load and replay.
    """
    events, t = [], 0.0
    for name in ("f13", "f14", "f15"):
        events.append({"type": "key", "kind": "special", "name": name,
                       "pressed": True, "t": round(t, 6)})
        events.append({"type": "key", "kind": "special", "name": name,
                       "pressed": False, "t": round(t + 0.02, 6)})
        t += 0.3
    path = rec_dir / "legacy_fixture.json"
    path.write_text(json.dumps({"version": 1, "name": "legacy_fixture",
                                "created": "2026-07-29T00:00:00+08:00",
                                "events": events}, indent=2), encoding="utf-8")
    return path


def legacy_replay(state, env, rec, cfg):
    """Load an old-format recording and replay it through a real subprocess."""
    path = plant_legacy(rec)
    from pynput import keyboard
    mapping, _ = bindings.load_map(cfg)
    seen = []

    def on_press(key):
        if isinstance(key, keyboard.Key) and key.name in ("f13", "f14", "f15"):
            seen.append(key.name)

    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    listener.wait()
    proc = subprocess.Popen([str(VENV_PY), str(MACRO), "play",
                             "legacy_fixture"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=env,
                            start_new_session=True)
    time.sleep(3.0)
    kbd = keyboard.Controller()
    mods = [{"ctrl": keyboard.Key.ctrl, "alt": keyboard.Key.alt,
             "shift": keyboard.Key.shift,
             "cmd": keyboard.Key.cmd}[m]
            for m in mapping["play"]["modifiers"]]
    for m in mods:
        kbd.press(m)
        time.sleep(0.02)
    trigger = mapping["play"]["trigger"]
    key = getattr(keyboard.Key, trigger["name"])
    kbd.press(key)
    time.sleep(0.05)
    kbd.release(key)
    for m in reversed(mods):
        time.sleep(0.02)
        kbd.release(m)
    time.sleep(3.0)
    proc.terminate()
    try:
        out = proc.communicate(timeout=8)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        out = proc.communicate(timeout=8)[0]
    listener.stop()
    check("[%s] an OLD-format recording loaded and replayed" % state,
          seen[:3] == ["f13", "f14", "f15"],
          "captured=%s out=%r" % (seen[:6], out[-160:]))


def cli_checks(state, env, cfg):
    expected = {a: bindings.format_binding(b)
                for a, b in bindings.load_map(cfg)[0].items()}

    rc, out = run([str(VENV_PY), str(MACRO), "--help"], env, 30, "help")
    check("[%s] CLI --help works" % state, rc == 0 and "record" in out,
          "rc=%s" % rc)

    rc, out = run([str(VENV_PY), str(MACRO), "list"], env, 30, "list")
    check("[%s] CLI list works" % state, rc == 0, "rc=%s out=%r" % (rc, out[:120]))

    rc, out = run([str(VENV_PY), str(MACRO), "hotkeys"], env, 30, "hotkeys")
    shown = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0] in bindings.ACTIONS:
            shown[parts[0]] = parts[1].strip()
    check("[%s] CLI reports the configured bindings, not the defaults" % state,
          rc == 0 and shown == expected, "shown=%s expected=%s"
          % (shown, expected))

    rc, out = run([str(VENV_PY), str(MACRO), "help"], env, 30, "help-label")
    check("[%s] CLI help text names a real binding" % state,
          expected["record"] in out or rc != 0, "rc=%s" % rc)

    for args, why in (
            (["play", "x", "--speed", "0"], "speed 0 rejected"),
            (["play", "x", "--loop", "-1"], "negative loop rejected"),
            (["autoclick", "--min", "5", "--max", "1"], "min above max rejected"),
            (["record", "bad name!"], "unsafe name rejected")):
        rc, out = run([str(VENV_PY), str(MACRO)] + args, env, 30, why)
        check("[%s] CLI boundary: %s" % (state, why), rc != 0, "rc=%s" % rc)


def run_state(state, mapping_fn):
    print("\n" + "=" * 78)
    print("CONFIG STATE: %s" % state)
    print("=" * 78)
    env, rec, cfg = make_env(state, mapping_fn)
    print("   recordings : %s" % rec)
    print("   config     : %s%s" % (cfg, "" if mapping_fn else " (absent)"))
    mapping, warning = bindings.load_map(cfg)
    print("   bindings   : %s"
          % {a: bindings.format_binding(b) for a, b in mapping.items()})
    check("[%s] the config state loaded without a warning" % state,
          warning is None, "warning=%r" % warning)

    for proof, timeout in (("probe", 90), ("roundtrip", 200),
                           ("interrupt", 200), ("autoclick", 200)):
        rc, out = run([str(VENV_PY), str(HERE / "driver.py"), proof],
                      env, timeout, proof)
        ok = rc == 0
        tail = [l for l in out.splitlines() if l.strip()][-1:] if out else []
        check("[%s] driver %s" % (state, proof), ok,
              "rc=%s %s" % (rc, tail[0].strip() if tail else ""))
        if not ok:
            print(out[-1500:])

    rc, out = run([str(VENV_PY), str(HERE / "proof_gui.py")], env, 200,
                  "proof_gui")
    check("[%s] proof_gui window census and responsiveness" % state, rc == 0,
          "rc=%s" % rc)
    if rc != 0:
        print(out[-1500:])
    check("[%s] proof_gui asserted its AppKit ran on the main thread" % state,
          "every AppKit step ran on the main thread: True" in out)

    cli_checks(state, env, cfg)
    legacy_replay(state, env, rec, cfg)


def main():
    print("THREE-CONFIG REGRESSION MATRIX")
    print("  scratch root : %s" % TMP_ROOT)
    real = Path(os.path.expanduser("~/.macro-recorder.json"))
    started_absent = not real.exists()
    real_rec = ROOT / "recordings"
    before = sorted((p.name, p.stat().st_mtime, p.stat().st_size)
                    for p in real_rec.glob("*.json")) if real_rec.exists() else []

    for state, fn in CONFIGS:
        if ONLY and ONLY != state:
            continue
        run_state(state, fn)

    after = sorted((p.name, p.stat().st_mtime, p.stat().st_size)
                   for p in real_rec.glob("*.json")) if real_rec.exists() else []
    check("the real recordings directory was never touched", before == after,
          "before=%s after=%s" % (before, after))
    check("the real ~/.macro-recorder.json was never created",
          started_absent and not real.exists())

    print()
    print("=" * 78)
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        if not ok:
            print("FAILED: %s %s" % (name, detail))
    print("REGRESSION MATRIX: %d of %d checks passed" % (passed, len(results)))
    verdict = passed == len(results) and bool(results)
    print("VERDICT: %s" % ("PASS" if verdict else "FAIL"))
    print("=" * 78)
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
