# Gate B: the five minute morning check

**Read this cold. Nothing here needs anything from last night in your head.**

Everything else about this fix has been proved by a computer pressing keys. That is not the same as your keyboard pressing keys, and the difference is exactly what broke the tool the first time: a computer generated keypress arrives already labelled F6, while your actual top row sends something else entirely. Until your fingers do it, the fix is not proven and nothing gets pushed.

This takes about five minutes and needs no decisions from you.

---

## Before you start, the one thing worth knowing

Your keyboard's top row does **not** send F6, F7 and F8 by default. This was measured on your Mac on 30 July:

| You press | What your Mac actually sends | Can the tool see it? |
| --- | --- | --- |
| F6 alone | a key with code 178 | No |
| F7 alone | a media REWIND signal | No, nothing arrives at all |
| F8 alone | media play and pause | No |
| **fn held**, then F6 / F7 / F8 | real F6, F7 and F8 | **Yes** |

So every prompt below says **hold fn**. That is not a workaround for a bug in the tool, it is what your keyboard does. Step 2 makes you watch it happen.

---

## Run it

Open Terminal and paste this one line:

```
cd ~/Documents/macro-recorder && ./.venv/bin/python tests/gate_b.py
```

A window opens and the Terminal prints one prompt at a time. Each prompt waits **25 seconds**. Do the thing it asks, watch the result, and it moves on by itself.

Nothing of yours is touched: it uses a throwaway recordings folder and a throwaway settings file, it prints the checksums of your real recordings before and after, and the only keys it plays back are F13 to F15, which your Mac does nothing with. The autoclicker clicks inside a blank window the test opens for itself.

---

## The prompts, in order, and what you should see

### 1. An ordinary key

> **Press and release the letter A, once.**

You should see: **Key seen**

If you see *Nothing seen*, stop. Permissions are not live for Terminal. The Test Input window names the app to switch on.

### 2. The defect itself, made visible

> **Press and release F7 on its own. No fn key.**

You should see: **Seen, but unusable: rewind (media key)**

This is the whole bug in one line. You pressed F7, your Mac sent a rewind signal, and no app binding F7 could ever have seen it. This is the tool correctly reporting a keyboard fact, not a failure.

### 3 and 4. Recording, two full cycles

> **Cycle 1 of 2: hold fn and press F6 to START recording**

You should see: the status line changes to **Recording 'gate_b_take'**

> **Cycle 1 of 2: hold fn and press F6 again to STOP**

You should see: the status line returns to **Idle** and mentions events saved

> **Cycle 2 of 2: hold fn and press F6 to START recording**

Same as cycle 1: **Recording 'gate_b_take'**

> **Cycle 2 of 2: hold fn and press F6 again to STOP**

Same as cycle 1: back to **Idle**

Two full cycles, because starting once proves the key arrives; starting, stopping and starting **again** proves the key re-arms. A hotkey that fires once and then goes deaf would pass a single cycle.

### 5 and 6. Playback, two full cycles

Playback is deliberately set to **Loop forever** before you press anything, so it cannot finish on its own. If it goes idle, only your keypress can have done it.

> **Cycle 1 of 2: hold fn and press F7 to START playback**

You should see: **Playing 'gate_b_harmless', repeating forever**

> **Cycle 1 of 2: hold fn and press F7 again to STOP**

You should see: back to **Idle**

> **Cycle 2 of 2: hold fn and press F7 to START playback**

Same: **Playing 'gate_b_harmless', repeating forever**

> **Cycle 2 of 2: hold fn and press F7 again to STOP**

Same: back to **Idle**

### 7 and 8. Autoclicker, two full cycles

A blank window opens for this and the cursor is parked inside it. Every click lands there.

> **Cycle 1 of 2: hold fn and press F8 to START the autoclicker**

You should see: **Autoclicking**, with the click count climbing

> **Cycle 1 of 2: hold fn and press F8 again to STOP**

You should see: back to **Idle**

> **Cycle 2 of 2: hold fn and press F8 to START the autoclicker**

Same: **Autoclicking**, count climbing

> **Cycle 2 of 2: hold fn and press F8 again to STOP**

Same: back to **Idle**

---

## At the end

The last block prints one of these:

- **VERDICT: PASS** means the hotkeys work under your real fingers. Tell me and I push.
- **VERDICT: FAILED** means a step did not do what it should. Send me the block. Nothing gets pushed.
- **VERDICT: INCOMPLETE** means a prompt timed out, usually because you stepped away. Just run it again. Nothing gets pushed.

Under the verdict it reprints the checksums of your real recordings so you can see for yourself that nothing of yours changed.

---

## If you would rather not hold fn ever again

Two options, both permanent, both your call and neither needed for this check:

- **System Settings**, then **Keyboard**, then **Keyboard Shortcuts**, then **Function Keys**, then turn ON *Use F1, F2, etc. keys as standard function keys*. Your top row becomes real F-keys and bare F6, F7 and F8 start working.
- Or open the window, go to the **Hotkeys** panel at the bottom, press **Set** on a row and press whatever key you actually want. **F13** and **F16** through **F19** are good picks: your Mac does nothing with them and neither do most games. It saves the moment you press it.

---

## What is still not claimed

Until this gate passes under your fingers, the hotkey defect is **not** claimed fixed end to end, and nothing is pushed. What is already proven, by machine, is that once a keypress reaches the tool every action completes with the right context, bindings survive editing and restarting, and a damaged settings file can never stop the tool opening. What only your fingers can prove is the step in front of all of that: that the key you press reaches the tool at all.
