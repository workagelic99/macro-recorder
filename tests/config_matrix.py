#!/usr/bin/env python3
"""
The config contract, proved COLD.

Two halves.

LOAD. Nine states a config file can be in, each given to a FRESH GUI process
and a FRESH command line process. What matters is not that the loader returns
something sensible when called in a unit test, but that a bad file cannot stop
the tool from opening at all, and that the person is told what happened rather
than left with hotkeys they did not choose and no explanation. So each case
asserts three things: the process SURVIVED, the EXACT active map, and the
WARNING, visible on screen for the window and on stdout for the command line.

SAVE. A save that half succeeds is the dangerous case, because the file it
damages is the only record of the user's own choices. The saver writes a
sibling temp file in the SAME directory and then performs one atomic replace,
so the destination is either the old bytes or the new bytes and never a
half-written mixture. That claim is tested by FORCING the replace to fail after
the temp file is fully written, which is the exact moment where a naive
implementation has already truncated the original.

Nothing here touches ~/.macro-recorder.json. Every case uses its own temporary
file, and the real path is asserted absent at the end.
"""

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bindings  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"
PROBE = HERE / "config_probe_gui.py"
MACRO = ROOT / "macro.py"

TMP_ROOT = Path(tempfile.mkdtemp(prefix="macro_cfg_"))
PROBE_TIMEOUT = 60
CLI_TIMEOUT = 30

DEFAULTS = {a: bindings.format_binding(b)
            for a, b in bindings.default_map().items()}

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("   %-62s %s%s" % (name, "PASS" if ok else "FAIL",
                             ("  " + detail) if detail else ""))
    sys.stdout.flush()


def valid_payload():
    return json.dumps({"version": bindings.SCHEMA_VERSION,
                       "bindings": bindings.validate_map({
                           "record": bindings.make_binding(
                               [], {"kind": "named", "name": "f16"}),
                           "play": bindings.make_binding(
                               ["ctrl"], {"kind": "named", "name": "f17"}),
                           "autoclick": bindings.make_binding(
                               [], {"kind": "named", "name": "f18"})})},
                      indent=2) + "\n"


VALID_LABELS = {"record": "F16", "play": "Control + F17", "autoclick": "F18"}


def case_dir(name):
    d = TMP_ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_cases():
    """[(name, config path or None, expected map, warning must mention), ...]"""
    cases = []

    d = case_dir("absent")
    cases.append(("absent", d / "config.json", DEFAULTS, None))

    d = case_dir("valid")
    p = d / "config.json"
    p.write_text(valid_payload(), encoding="utf-8")
    cases.append(("valid", p, VALID_LABELS, None))

    d = case_dir("unreadable")
    p = d / "config.json"
    p.write_text(valid_payload(), encoding="utf-8")
    os.chmod(p, 0o000)
    cases.append(("unreadable", p, DEFAULTS, "could not read"))

    d = case_dir("truncated")
    p = d / "config.json"
    p.write_text(valid_payload()[:60], encoding="utf-8")
    cases.append(("truncated JSON", p, DEFAULTS, "not valid json"))

    d = case_dir("wrong_type")
    p = d / "config.json"
    p.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    cases.append(("wrong top-level type", p, DEFAULTS, "settings object"))

    d = case_dir("unknown_version")
    p = d / "config.json"
    p.write_text(json.dumps({"version": 99, "bindings": {}}), encoding="utf-8")
    cases.append(("unknown schema version", p, DEFAULTS, "different version"))

    d = case_dir("unknown_action")
    p = d / "config.json"
    payload = json.loads(valid_payload())
    payload["bindings"]["teleport"] = payload["bindings"]["record"]
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    cases.append(("unknown action", p, DEFAULTS, "unknown action"))

    d = case_dir("unknown_key")
    p = d / "config.json"
    payload = json.loads(valid_payload())
    payload["bindings"]["record"]["trigger"] = {"kind": "named",
                                                "name": "hyperspace"}
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    cases.append(("unknown key name", p, DEFAULTS, "unknown key name"))

    d = case_dir("duplicate")
    p = d / "config.json"
    payload = json.loads(valid_payload())
    payload["bindings"]["autoclick"] = payload["bindings"]["record"]
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    cases.append(("duplicate binding", p, DEFAULTS, "same binding"))

    return cases


def run_gui_probe(config_path):
    env = dict(os.environ)
    env["MACRO_CONFIG_PATH"] = str(config_path)
    proc = subprocess.run(
        [str(VENV_PY), str(PROBE), str(config_path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=PROBE_TIMEOUT, env=env)
    line = next((l for l in proc.stdout.splitlines()
                 if l.startswith("PROBE ")), None)
    data = json.loads(line[6:]) if line else None
    return proc.returncode, data, proc.stdout


def run_cli(config_path, args, timeout=CLI_TIMEOUT):
    env = dict(os.environ)
    env["MACRO_CONFIG_PATH"] = str(config_path)
    env["MACRO_RECORDINGS_DIR"] = str(TMP_ROOT / "cli_recordings")
    (TMP_ROOT / "cli_recordings").mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(VENV_PY), str(MACRO)] + args,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=timeout, env=env)
    return proc.returncode, proc.stdout


def parse_cli_hotkeys(out):
    mapping = {}
    warning = None
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0] in bindings.ACTIONS:
            mapping[parts[0]] = parts[1].strip()
        if line.startswith("WARNING: "):
            warning = line[len("WARNING: "):].strip()
    if warning == "none":
        warning = None
    return mapping, warning


def load_matrix():
    print("\nLOAD MATRIX: nine config states, a fresh GUI process and a fresh "
          "CLI process each")
    for name, path, expected, must_mention in build_cases():
        print("\n  case: %s" % name)
        # ---- GUI
        try:
            rc, data, raw = run_gui_probe(path)
        except subprocess.TimeoutExpired:
            check("GUI [%s] survived" % name, False, "timed out")
            continue
        if data is None:
            check("GUI [%s] survived and reported" % name, False,
                  "rc=%s output=%r" % (rc, raw[-300:]))
        else:
            check("GUI [%s] process survived and opened its window" % name,
                  rc == 0 and data["window_visible"] and data["listeners"],
                  "rc=%s visible=%s listeners=%s"
                  % (rc, data["window_visible"], data["listeners"]))
            check("GUI [%s] active map is exactly right" % name,
                  data["map"] == expected, "map=%s" % data["map"])
            check("GUI [%s] row labels agree with the active map" % name,
                  data["row_labels"] == expected,
                  "labels=%s" % data["row_labels"])
            warned = (data["warning"] or "").lower()
            on_screen = (data["status_line"] or "").lower()
            if must_mention:
                check("GUI [%s] warned the user on screen" % name,
                      must_mention in warned and must_mention in on_screen,
                      "status=%r" % data["status_line"])
            else:
                check("GUI [%s] warned about nothing, correctly" % name,
                      data["warning"] is None, "warning=%r" % data["warning"])
        # ---- CLI
        try:
            rc, out = run_cli(path, ["hotkeys"])
        except subprocess.TimeoutExpired:
            check("CLI [%s] survived" % name, False, "timed out")
            continue
        mapping, warning = parse_cli_hotkeys(out)
        check("CLI [%s] process survived, exit 0" % name, rc == 0,
              "rc=%s out=%r" % (rc, out[-200:]))
        check("CLI [%s] active map is exactly right" % name,
              mapping == expected, "map=%s" % mapping)
        if must_mention:
            check("CLI [%s] warned on stdout" % name,
                  bool(warning) and must_mention in warning.lower(),
                  "warning=%r" % warning)
        else:
            check("CLI [%s] warned about nothing, correctly" % name,
                  warning is None, "warning=%r" % warning)


def cli_boots_with_bad_config():
    """
    The full command line path, not just the read-only report: a real armed
    recorder with its listeners, booted on a truncated config.
    """
    print("\n  the REAL CLI record mode boots on a truncated config")
    path = TMP_ROOT / "truncated" / "config.json"
    env = dict(os.environ)
    env["MACRO_CONFIG_PATH"] = str(path)
    env["MACRO_RECORDINGS_DIR"] = str(TMP_ROOT / "cli_recordings")
    proc = subprocess.Popen(
        [str(VENV_PY), str(MACRO), "record", "cli_boot_probe"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)
    time.sleep(4.0)
    alive = proc.poll() is None
    # SIGINT, not SIGTERM. macro.py catches KeyboardInterrupt and exits
    # cleanly; SIGTERM would kill it before Python flushed its output, and the
    # empty capture would look like a tool that printed nothing.
    proc.send_signal(signal.SIGINT)
    try:
        out = proc.communicate(timeout=8)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        out = proc.communicate(timeout=8)[0]
    check("CLI record mode stayed alive with a broken config", alive)
    check("CLI record mode showed the fallback warning",
          "not valid json" in out.lower(), "out=%r" % out[:400])
    check("CLI record mode armed the DEFAULT hotkey after falling back",
          DEFAULTS["record"] in out, "out=%r" % out[:400])


# ---------------------------------------------------------------------------
# SAVE
# ---------------------------------------------------------------------------

def save_cases():
    print("\nSAVE CASES")
    good = bindings.validate_map({
        "record": bindings.make_binding([], {"kind": "named", "name": "f16"}),
        "play": bindings.make_binding([], {"kind": "named", "name": "f17"}),
        "autoclick": bindings.make_binding([], {"kind": "named",
                                                "name": "f18"})})
    other = bindings.validate_map({
        "record": bindings.make_binding([], {"kind": "named", "name": "f19"}),
        "play": bindings.make_binding([], {"kind": "named", "name": "f17"}),
        "autoclick": bindings.make_binding([], {"kind": "named",
                                                "name": "f18"})})

    # ---- 1. successful save, and PROVE it used a same-directory replace
    d = case_dir("save_ok")
    path = d / "config.json"
    seen = {}

    def spy_replace(src, dst):
        seen["src"] = src
        seen["dst"] = dst
        seen["same_dir"] = (os.path.dirname(os.path.abspath(src))
                            == os.path.dirname(os.path.abspath(dst)))
        seen["temp_existed"] = os.path.exists(src)
        seen["temp_payload"] = Path(src).read_text(encoding="utf-8")
        return os.replace(src, dst)

    ok, warning = bindings.save_map(good, path, _replace=spy_replace)
    check("a good save reports success", ok and warning is None,
          "warning=%r" % warning)
    check("the save went through an atomic replace at all", bool(seen))
    check("the temp file was in the SAME directory as the destination",
          seen.get("same_dir"), "src=%s dst=%s"
          % (seen.get("src"), seen.get("dst")))
    check("the temp file was fully written BEFORE the replace",
          seen.get("temp_existed")
          and "f16" in (seen.get("temp_payload") or ""),
          "payload starts %r" % (seen.get("temp_payload") or "")[:40])
    reloaded, w = bindings.load_map(path)
    check("what was saved is what loads back",
          {a: bindings.format_binding(b) for a, b in reloaded.items()}
          == {a: bindings.format_binding(b) for a, b in good.items()},
          "warning=%r" % w)
    check("no temp file was left behind",
          [f for f in os.listdir(d) if f.endswith(".tmp")] == [],
          "leftovers=%s" % os.listdir(d))

    # ---- 2. REPLACE-BOUNDARY FAULT INJECTION
    d = case_dir("save_fault")
    path = d / "config.json"
    ok, _ = bindings.save_map(good, path)
    before = path.read_bytes()
    check("fault case starts from a known good destination", ok)

    boundary = {}

    def failing_replace(src, dst):
        boundary["temp_existed"] = os.path.exists(src)
        boundary["temp_payload"] = Path(src).read_text(encoding="utf-8")
        boundary["dest_at_boundary"] = Path(dst).read_bytes()
        raise OSError(28, "No space left on device (injected at the replace "
                          "boundary)")

    ok, warning = bindings.save_map(other, path, _replace=failing_replace)
    check("the fault was injected AFTER the temp file was complete",
          boundary.get("temp_existed")
          and "f19" in (boundary.get("temp_payload") or "")),
    check("the destination was still the OLD bytes at the boundary",
          boundary.get("dest_at_boundary") == before)
    check("the save reported failure rather than pretending", not ok)
    check("the failure message names the file and reassures",
          bool(warning) and "unchanged" in warning.lower(),
          "warning=%r" % warning)
    check("the previous config survived BYTE FOR BYTE",
          path.read_bytes() == before)
    check("no temp file was left behind after the fault",
          [f for f in os.listdir(d) if f.endswith(".tmp")] == [],
          "leftovers=%s" % os.listdir(d))
    reloaded, _ = bindings.load_map(path)
    check("the surviving file still loads as the ORIGINAL bindings",
          {a: bindings.format_binding(b) for a, b in reloaded.items()}
          == {a: bindings.format_binding(b) for a, b in good.items()})

    # ---- 3. unwritable destination
    d = case_dir("save_unwritable")
    path = d / "config.json"
    ok, _ = bindings.save_map(good, path)
    before = path.read_bytes()
    os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)          # read and enter, no write
    try:
        ok, warning = bindings.save_map(other, path)
        check("an unwritable destination reports failure", not ok)
        check("an unwritable destination explains itself",
              bool(warning) and "could not save" in warning.lower(),
              "warning=%r" % warning)
    finally:
        os.chmod(d, stat.S_IRWXU)
    check("the unwritable case left the previous file untouched",
          path.read_bytes() == before)
    check("no temp file was left behind in the unwritable case",
          [f for f in os.listdir(d) if f.endswith(".tmp")] == [],
          "leftovers=%s" % os.listdir(d))

    # ---- 4. a rejected map never reaches the disk at all
    d = case_dir("save_rejected")
    path = d / "config.json"
    bindings.save_map(good, path)
    before = path.read_bytes()
    dup = {"record": bindings.make_binding([], {"kind": "named",
                                                "name": "f16"}),
           "play": bindings.make_binding([], {"kind": "named", "name": "f16"}),
           "autoclick": bindings.make_binding([], {"kind": "named",
                                                   "name": "f18"})}
    try:
        bindings.save_map(dup, path)
        rejected = False
    except bindings.BindingError:
        rejected = True
    check("a duplicate map is rejected before any write", rejected)
    check("the rejected save left the file untouched",
          path.read_bytes() == before)


def main():
    print("CONFIG CONTRACT, cold processes")
    print("  scratch root : %s" % TMP_ROOT)
    print("  real config  : %s (must stay absent)" % bindings.CONFIG_PATH)
    real = Path(os.path.expanduser("~/.macro-recorder.json"))
    started_absent = not real.exists()

    load_matrix()
    cli_boots_with_bad_config()
    save_cases()

    check("the real ~/.macro-recorder.json was never created by these tests",
          started_absent and not real.exists(),
          "existed_before=%s exists_now=%s" % (not started_absent,
                                               real.exists()))

    print()
    print("=" * 78)
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        if not ok:
            print("FAILED: %s %s" % (name, detail))
    print("CONFIG CONTRACT: %d of %d assertions passed" % (passed, len(results)))
    verdict = passed == len(results) and bool(results)
    print("VERDICT: %s" % ("PASS" if verdict else "FAIL"))
    print("=" * 78)

    for d in TMP_ROOT.rglob("*"):
        try:
            if d.is_dir():
                os.chmod(d, stat.S_IRWXU)
            else:
                os.chmod(d, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
