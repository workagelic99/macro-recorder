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
- Python 3 with Tk, which Apple already ships at `/usr/bin/python3`. The install steps below use it on purpose. Homebrew's Python usually has no Tk, and the window will not open on it.

## Install

Open **Terminal** (press `Cmd+Space`, type `Terminal`, press Return) and paste these lines one at a time:

```
git clone https://github.com/workagelic99/macro-recorder.git
cd macro-recorder
/usr/bin/python3 -m venv .venv
./.venv/bin/python -m pip install pynput
```

That last command creates a private Python setup inside the project folder. It does not touch the rest of your system.

## Then grant two permissions. This part is not optional.

macOS will not let any program watch your keyboard or move your mouse until you allow it. You do this once. It takes about two minutes.

The permission is granted to **Terminal**, not to this project, because Terminal is the app that runs it.

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

You get one window with everything in it:

- A list of your saved recordings
- A **Save as** box, and **Record**, **Stop** and **Play** buttons
- A **Speed** box and a **Repeat** box, plus a **Loop forever** tickbox
- An **Autoclicker** section with **Min ms** and **Max ms** boxes and a start and stop button
- A **status line** along the bottom that always says exactly what is happening, for example `Playing 'farm_loop': loop 3 of 10`

The hotkeys below keep working while the window is open, so you can leave it off to one side and drive everything from the keyboard. The window stays responsive while a macro is playing.

## The hotkeys

| Key | What it does |
| --- | --- |
| **F6** | Start and stop recording |
| **F7** | Start and stop playback |
| **F8** | Start and stop the autoclicker |

Press the same key again to stop. `Ctrl+C` in the Terminal window quits.

### If pressing F6 changes your screen brightness instead

On a Mac keyboard the F-keys are shortcuts by default. Two options:

- **Easy:** hold the **fn** key while pressing F6, F7 or F8
- **Permanent:** System Settings, then **Keyboard**, then **Keyboard Shortcuts**, then **Function Keys**, then turn ON *Use F1, F2, etc. keys as standard function keys*

### If your game already uses F6, F7 or F8

Plenty of games bind the F-keys to their own actions. If yours does, open `macro.py` and change these three lines in the CONFIG block at the top:

```python
HOTKEY_RECORD = keyboard.Key.f6
HOTKEY_PLAY = keyboard.Key.f7
HOTKEY_AUTOCLICK = keyboard.Key.f8
```

Good alternatives that games rarely touch: `keyboard.Key.f13`, `keyboard.Key.pause`, `keyboard.Key.scroll_lock`.

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
  macro.py        the engine plus the command line
  gui.py          the window, a front end over the same engine
  tests/          self-driving proof scripts
  recordings/     your saved sequences, one JSON file each (not committed)
  .venv/          the private Python install (not committed)
```

## If something is not working

**Nothing gets recorded and F6 does nothing.** The permissions are not actually live. Check both switches are ON, and make sure you quit Terminal with `Cmd+Q` and opened it again.

**It refuses to start and mentions a listener dying.** Same cause: Input Monitoring is not granted, or Terminal was not restarted after granting it.

**The window will not open, and you see an error about `_tkinter`.** You built the virtual environment with a Python that has no Tk. Delete the `.venv` folder and build it again with `/usr/bin/python3` as shown in the install steps.

**F6 changes screen brightness.** See the fn key note above.

**Playback clicks the wrong spot.** The window moved or the resolution changed. Record it again.

**It clicks in the game but nothing happens.** That is the anti-cheat limitation above. There is nothing to fix.

## Running the tests

The `tests/` folder drives the tool for real: it launches it as a separate process, synthesizes input, and reads back what actually happened. Test clicks are confined to a plain window the harness creates, and the only keys used are F13, F14 and F15, which macOS binds to nothing.

```
./.venv/bin/python tests/driver.py probe
./.venv/bin/python tests/driver.py roundtrip
./.venv/bin/python tests/driver.py interrupt
./.venv/bin/python tests/driver.py autoclick
./.venv/bin/python tests/proof_gui.py
```

## Licence

This project is MIT licensed. See [LICENSE](LICENSE).

It depends on [pynput](https://github.com/moses-palmer/pynput), which is **LGPL v3**. pynput is installed separately by pip and is not included in this repository, so the MIT licence here covers this project's own code. If you redistribute a bundle that contains pynput itself, read the LGPL terms first.

## A note on two macOS quirks, for anyone reading the code

Both cost real debugging time and are commented in the source:

1. **Start the pynput listeners before creating the Tk root.** pynput installs a CGEventTap and spins a CFRunLoop. If Tk initialises the main run loop first, the process dies with `SIGABRT` and prints no traceback whatsoever. Measured on macOS 26.4.1 with Tk 8.5: Tk first aborted 3 runs out of 3, listeners first survived every time.
2. **Resolve `AXIsProcessTrusted` once before starting both listeners.** pyobjc resolves that symbol lazily with an unguarded `funcmap.pop(name)`. Starting the keyboard and mouse listeners in the same instant makes both threads race for it, and the loser dies with `KeyError: 'AXIsProcessTrusted'`, silently taking the hotkeys with it.
