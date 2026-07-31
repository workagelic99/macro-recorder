# Macro Recorder

A small macOS tool that records your mouse clicks and keystrokes, then plays them back exactly where and when you made them. Built for repetitive click sequences, so you stop wearing out your hands doing the same thing over and over.

There is a window with buttons, and a command line, and they both do the same things. Everything runs on your own Mac. Nothing is sent anywhere, there is no account, and there is no network code in this project at all.

## What it does

- **Record** your clicks, scrolls and keystrokes, with the timing preserved
- **Play them back** at the exact screen positions they were recorded at
- **Speed them up or slow them down**, and repeat a set number of times or forever
- **Autoclicker** that clicks where your cursor is, with a randomised human-like gap between clicks that you choose
- **Global hotkeys** so you can start and stop without alt-tabbing back to the window

## Requirements

- A Mac
- Any Python 3. The window is built with macOS's own Cocoa toolkit through pyobjc, which pip installs automatically as part of pynput, so there is nothing else to set up.

## Install

Open **Terminal** (press `Cmd+Space`, type `Terminal`, press Return) and paste these lines one at a time:

```
git clone https://github.com/workagelic99/macro-recorder.git
cd macro-recorder
python3 -m venv .venv
./.venv/bin/python -m pip install pynput
```

That last command creates a private Python setup inside the project folder. It does not touch the rest of your system.

## Then grant two permissions. This part is not optional.

macOS will not let any program watch your keyboard or move your mouse until you allow it. You do this once. It takes about two minutes.

The permission is granted to **Terminal**, not to this project, because Terminal is the app that runs it.

**This is the single most confusing thing about macOS permissions, so it is worth being precise.** macOS does not grant these to a Python file. It grants them to the app RESPONSIBLE for running it, and the name in the System Settings list is that app's name:

| How you launch it | The name to look for in System Settings |
| --- | --- |
| Double clicking `Macro Recorder.command` in Finder | **Terminal** |
| Typing the command in Terminal | **Terminal** |
| Running it from inside a code editor's terminal | that editor's name, for example **Visual Studio Code** |

That is why the same tool can work perfectly in one place and do nothing in another: the permission followed the launcher, not the tool. Both of those cases were measured on this Mac, not assumed.

You never have to work this out yourself. The launcher prints the exact name before it opens the window, and the **Test Input** button in the window shows the same thing at any time.

### Permission 1 of 2: Input Monitoring, which lets it SEE your clicks and keys

1. Click the Apple menu in the top left corner, then **System Settings**
2. In the left sidebar, click **Privacy & Security**
3. Scroll down the right side and click **Input Monitoring**
4. Find **Terminal** in the list
   - If it is there, turn its switch **ON**
   - If it is not there, click the **+** button, press `Cmd+Shift+A` to jump to Applications, open the **Utilities** folder, click **Terminal**, then click **Open**
5. Enter your Mac password if it asks

### Permission 2 of 2: Accessibility, which lets it MOVE your mouse and press keys

1. Still in **Privacy & Security**, scroll and click **Accessibility**
2. Same as above: find **Terminal** and turn its switch **ON**, or click **+** and add it from Applications then Utilities
3. Enter your Mac password if it asks

### Now quit Terminal and open it again. Do not skip this.

Terminal only picks up new permissions when it starts fresh. If you skip this step the tool will appear to run but will record nothing at all.

1. Click on the Terminal window
2. Press `Cmd+Q`, or click **Terminal** in the menu bar then **Quit Terminal**
3. Open Terminal again, and `cd` back into the project folder

## Using the window

From the project folder:

```
./.venv/bin/python macro.py gui
```

Or just **double click `Macro Recorder.command`** in Finder. That opens a small Terminal window which prints which permissions are live, then opens the tool. Leave that Terminal window open while you use it.

You get one window with everything in it:

- A list of your saved recordings, with a **Delete** button beside it
- A **Save as** box, and **Record**, **Stop** and **Play** buttons. While you are recording, Stop reads **Stop & Save**, because that is exactly what it does: it ends the recording and writes it to disk under whatever is in the Save as box. You never have to hunt for a separate save step, and there is not one.
- A **Speed** box and a **Repeat** box, plus a **Loop forever** tickbox
- An **Autoclicker** section with **Min ms** and **Max ms** boxes and a start and stop button
- A **Hotkeys** panel where you change the three shortcuts, plus a **Test Input** button that tells you whether a key is reaching the tool at all
- A **status line** along the bottom that always says exactly what is happening, for example `Playing 'farm_loop': loop 3 of 10`

The hotkeys below keep working while the window is open, so you can leave it off to one side and drive everything from the keyboard. The window stays responsive while a macro is playing.

### Deleting a recording

Click the one you want in the list, then press **Delete**. It asks before it does anything:

1. The button changes to **Delete 'that_name'?** and the status line spells out which recording it means and that this cannot be undone
2. Press **Delete** again to go through with it, or press **Cancel** to keep it

Only the recording you selected is ever touched, and its file is removed from the `recordings/` folder. There is no undo and nothing goes to the Trash, which is why it asks twice.

Three things it deliberately refuses to do, each with the reason on the status line rather than a button that just sits there:

- Nothing selected: it tells you to pick a recording first.
- Something is recording or playing: it tells you to press Stop first. Deleting a file while it is being played back is a confusing way to fail, and the recording being written right now is the one most worth keeping.
- If you click a different recording while it is asking, it forgets the question. The second press can never land on something you did not aim at.

## The hotkeys

| Key | What it does |
| --- | --- |
| **F6** | Start and stop recording |
| **F7** | Start and stop playback |
| **F8** | Start and stop the autoclicker |

Press the same key again to stop. `Ctrl+C` in the Terminal window quits.

You can change all three from the **Hotkeys** panel in the window. See below.

### What your top row actually sends, measured on this Mac

This is the part that matters most, and it is not a guess. The keys were pressed on this keyboard and every layer was recorded.

By default macOS treats the top row as media and brightness keys, not as F-keys. So when you press them **without holding fn**, here is what actually leaves the keyboard:

| You press | What macOS actually sends | Can this tool see it? |
| --- | --- | --- |
| F6 on its own | a key with code 178, which is not F6 | No |
| F7 on its own | a media REWIND event | No, nothing arrives at all |
| F8 on its own | a media play and pause event | No |
| **fn** held, then F6, F7 or F8 | genuine F6, F7 and F8 | **Yes** |

So with the factory settings, **bare F6, F7 and F8 cannot work**, and that is a property of the keyboard, not a bug in this tool. Three ways to fix it, pick one:

- **Easiest:** hold **fn** (or the Globe key) while pressing F6, F7 or F8. Works immediately, nothing to change.
- **Permanent:** System Settings, then **Keyboard**, then **Keyboard Shortcuts**, then **Function Keys**, then turn ON *Use F1, F2, etc. keys as standard function keys*. After that the bare keys work.
- **Best:** open the **Hotkeys** panel in the window and pick keys you can actually press.

Not sure whether a key reaches the tool? Press **Test Input** in the window and press that key. It tells you one of three things: the key was seen and can be used, the key was seen but arrives as a media key this tool cannot read, or nothing arrived at all.

### Changing the hotkeys, in the window

The **Hotkeys** panel at the bottom of the window has one row per action:

1. Click **Set** on the row you want to change
2. Press the key you want. Hold Shift, Control, Option or Command first if you want a combination
3. That row now shows the new key, and it is saved straight away

- **Cancel** on that row abandons the change and keeps the old key.
- **Reset Defaults** puts all three back to F6, F7 and F8.
- Two actions cannot share the same key. If you try, the tool says so and changes nothing.
- Printable keys are remembered as the **physical key**, so holding Shift or Option does not turn your binding into a different one.
- The **fn** key cannot be part of a combination. macOS does not report it in a way any app can rely on, so a hotkey that needed it would be unreliable in a way you could not see.

If your game already uses F6, F7 or F8, this panel is the answer. Keys games rarely touch: **F13**, **F16** through **F19**, **Pause**, **Scroll Lock**.

### Where your hotkeys are saved

In a file at `~/.macro-recorder.json`. You never need to open it, but it is plain readable JSON if you want to.

The rules it follows, so a bad file can never lock you out:

- If the file is missing, damaged, unreadable, written by a newer version, or names a key or action this version does not know, the tool **still opens**, falls back to the built-in F6, F7 and F8, and says so in the status line at the bottom of the window.
- Saving writes to a temporary file alongside it and then swaps it in one step, so an interrupted save leaves your previous settings intact rather than a half-written file.
- If a save fails for any reason, nothing changes: not the file, and not the hotkeys the running tool is using.

To see what a fresh start would use, without opening the window:

```
./.venv/bin/python macro.py hotkeys
```

## Using the command line instead

All commands run from inside the project folder.

**Record a sequence.** Press F6 to start, do your clicks, press F6 again to stop and save.

```
./.venv/bin/python macro.py record my_sequence
```

**Play it back.** Press F7 to start, F7 again to stop. Stop works at any time, including mid-loop.

```
./.venv/bin/python macro.py play my_sequence
```

**Twice as fast, repeating until you stop it:**

```
./.venv/bin/python macro.py play my_sequence --speed 2 --loop 0
```

- `--speed 2` is double speed, `--speed 0.5` is half speed. Default is 1.
- `--loop 5` repeats five times, `--loop 0` repeats forever. Default is 1.

**Autoclicker.** Clicks wherever your cursor is, pausing a random time between 80 and 140 milliseconds each time. Press F8 to start and stop.

```
./.venv/bin/python macro.py autoclick --min 80 --max 140
```

Pick whatever numbers you like. `--min 200 --max 900` is a slow, relaxed pace. The gap is randomised rather than fixed, which is closer to human timing than a perfectly even click rate.

**See what you have saved:**

```
./.venv/bin/python macro.py list
```

## Recording drags and drawn paths

By default the cursor **teleports** straight to each click. It does not trace the path your hand took between clicks. That keeps recordings small and playback reliable, and it is what you want for ordinary click sequences.

If you need a click-and-drag, or a hand-drawn path, open `macro.py` and set:

```python
CAPTURE_MOUSE_MOVES = True
```

Then record again. Recordings get much bigger and playback becomes more sensitive to timing, so turn it on only when you actually need a drag.

## Honest limitations

**Some games will not see these clicks at all.** This tool creates input the same way macOS does for accessibility software. Most apps and most games accept that without knowing the difference. But some games, particularly full-screen ones with anti-cheat systems, read the input hardware more directly or check whether an event came from a real physical device. In those games the clicks land nowhere and nothing happens. There is no setting here that changes that, and no attempt has been made to work around it.

Other things worth knowing:

- **Playback uses absolute screen coordinates.** If you move the window you were clicking on, change resolution, or plug in a different monitor, the clicks land in the wrong place. The tool warns you when the screen size has changed since the recording was made, but it cannot tell that you moved a window.
- **Timing is close, not perfect.** Playback lands within a few milliseconds of the recorded timing. macOS is not a real time operating system and will not do better.
- **Recordings are plain text.** A recording is a readable JSON file listing every key pressed while it was running. Do not record yourself typing a password. If you do, delete the file.
- **Automating an online game may break its rules.** That is between you and the game.

## Project layout

```
macro-recorder/
  macro.py                 the engine plus the command line
  gui.py                   the window (Cocoa via pyobjc), a front end over the same engine
  bindings.py              what a hotkey IS: identity, labels, saving and loading
  rawtap.py                watches the media keys pynput cannot see
  health.py                which permissions are live, and under whose name
  Macro Recorder.command   double click this in Finder to open the tool
  tests/                   self-driving proof scripts
  recordings/              your saved sequences, one JSON file each (not committed)
  .venv/                   the private Python install (not committed)
```

## If something is not working

**Nothing gets recorded and F6 does nothing.** Press **Test Input** in the window first, then press F6. It will tell you which of these it is:

- *Nothing seen* means the permissions are not actually live. Check both switches are ON for the app named in the Test Input window, then quit that app with `Cmd+Q` and open it again.
- *Seen, but unusable* means the key is reaching macOS but arriving as a media key. Hold **fn**, or turn on standard function keys, or pick a different key in the Hotkeys panel. See the measured table above.
- *Key seen* means the key is fine and the problem is elsewhere.

**It refuses to start and mentions a listener dying.** Input Monitoring is not granted, or the launching app was not restarted after granting it.

**F6 changes screen brightness.** That is the measured top row behaviour above. Hold fn, or change the key in the Hotkeys panel.

**My hotkeys went back to F6, F7 and F8 on their own.** The saved file could not be read, so the tool fell back to the defaults rather than refusing to open. The status line at the bottom of the window says exactly why. Set them again and they will save.

**Playback clicks the wrong spot.** The window moved or the resolution changed. Record it again.

**It clicks in the game but nothing happens.** That is the anti-cheat limitation above. There is nothing to fix.

## Running the tests

The `tests/` folder drives the tool for real: it launches it as a separate process, synthesizes input, and reads back what actually happened. Test clicks are confined to a plain window the harness creates, and the payload keys are F13, F14 and F15, which macOS binds to nothing.

Every test runs against a **throwaway recordings directory and a throwaway config file**, handed over through `MACRO_RECORDINGS_DIR` and `MACRO_CONFIG_PATH`. No test can read, change or create your real recordings or your real `~/.macro-recorder.json`, and several of them assert exactly that before they finish.

```
./.venv/bin/python tests/driver.py probe
./.venv/bin/python tests/driver.py roundtrip
./.venv/bin/python tests/driver.py interrupt
./.venv/bin/python tests/driver.py autoclick
./.venv/bin/python tests/proof_gui.py
./.venv/bin/python tests/chord_nonmatch.py
./.venv/bin/python tests/config_matrix.py
./.venv/bin/python tests/test_input_mode.py
./.venv/bin/python tests/gate_a.py i
./.venv/bin/python tests/gate_a.py ii
./.venv/bin/python tests/gate_a.py iii
./.venv/bin/python tests/delete_gate.py
./.venv/bin/python tests/regression_matrix.py
```

What the less obvious ones are for:

- `gate_a.py` starts every action by sending a real key through the OS and letting it come back via the listener. It never calls a button handler to start an action, because a proof that presses the button cannot tell you whether the hotkey works. Case `iii` edits bindings through the actual Set buttons, saves, and then fires the edited keys.
- `chord_nonmatch.py` covers the awkward half of combination hotkeys: a modifier arrives before the tool can know whether a hotkey is coming, so it is held back and then either dropped or put back into your recording at its original time.
- `config_matrix.py` gives nine different broken config files to a fresh window and a fresh command line each, and forces a save to fail at the exact moment the replacement happens.
- `delete_gate.py` is the strictest proof here, because deleting is the only thing this tool does that destroys something. Every press goes through the real button and every claim is checked against the disk: arming deletes nothing, Cancel deletes nothing, confirming removes exactly the named file while every other recording is checksummed before and after.
- `regression_matrix.py` runs everything above three times over: with no config, with saved plain keys, and with saved combinations.

## Licence

This project is MIT licensed. See [LICENSE](LICENSE).

It depends on [pynput](https://github.com/moses-palmer/pynput), which is **LGPL v3**. pynput is installed separately by pip and is not included in this repository, so the MIT licence here covers this project's own code. If you redistribute a bundle that contains pynput itself, read the LGPL terms first.

## A note on three macOS quirks, for anyone reading the code

All three cost real debugging time, all three are commented in the source, and
each one was found by a test rather than by reading the code:

1. **The window is Cocoa, not tkinter, and that was not a style choice.** tkinter was built first and abandoned on evidence. The only Python on macOS that ships Tk is Apple's `/usr/bin/python3`, carrying Tk 8.5.9 from 2010, which macOS itself marks deprecated. On macOS 26.4 that Tk lays every widget out correctly and reports all of them mapped and viewable, then draws none of them: the window opens at the right size with the right title and stays empty grey. It is not an interaction with pynput. Measured both ways, a window with no pynput anywhere in the process and a window with the listeners started first both mapped 5 of 5 children, identically. pyobjc is already required by pynput on macOS, so Cocoa costs no extra dependency and it draws. A related trap while Tk was still in play: starting the listeners after creating the Tk root aborted the process with `SIGABRT` and no traceback, 3 runs out of 3.
2. **Resolve `AXIsProcessTrusted` once before starting both listeners.** pyobjc resolves that symbol lazily with an unguarded `funcmap.pop(name)`. Starting the keyboard and mouse listeners in the same instant makes both threads race for it, and the loser dies with `KeyError: 'AXIsProcessTrusted'`, silently taking the hotkeys with it.
3. **Move the cursor early, press on schedule, and never re-assign the position just before the press.** macOS stamps a click with the cursor position it has already committed. Setting the position and pressing immediately makes every click land on the *previous* target, quietly shifting the whole sequence by one step, which looks like scrambled output rather than a timing bug. Playback therefore parks the cursor `MOVE_SETTLE_MS` ahead of each click and presses exactly on time, so correctness costs no timing accuracy. The first move of a run needs a longer settle than the rest, because the first cursor move in a process also pays for lazy symbol resolution; warming that path took worst-case replay error from about 26 ms down to under 4 ms.
