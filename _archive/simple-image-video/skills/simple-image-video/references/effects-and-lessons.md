# Effects catalog, props schema, and hard-won lessons

## The seamless-loop trick (why this works at all)

Every motion is `value = base + amp · sin(2π · k · t + φ)` where `t = frame / durationInFrames`
and **`k` is an integer**. At `t=0` and `t=1` the sine returns the same value, so frame 0 and
the last frame are identical — the loop has no seam and needs no crossfade. Particles use the
same idea: their vertical progress is `(phase + t·loops) % 1` with integer `loops`, and their
opacity fades to 0 at the wrap so you never see the jump.

**Consequence:** you can change the loop length freely (`durationInFrames`) and it stays seamless.
Longer loop = slower, dreamier pulses; shorter = punchier. 5–7 s is a good clip length; it is then
looped hundreds of times to fill the final video.

## Props schema (pass as `--props=./props.json` to `remotion render`)

```jsonc
{
  "image": "img.png",              // file in the remotion project's public/
  "width": 1920, "height": 1080,   // match the image aspect (cover-fit)
  "fps": 30, "durationInFrames": 210, // 210/30 = 7s loop
  "bg": "#0a0a12",
  "breath":   { "amp": 0.008, "k": 1 },              // whole-frame breathing
  "vignette": { "base": 0.34, "amp": 0.08, "k": 1 }, // breathing edge darkening
  "etherealGrade": true,           // cool spirit-light unifying wash (optional)

  // Bright pulsing glows — weapons, gems, lamps, eyes. Bright core + wide bloom.
  "halos": [
    { "cx": 0.40, "cy": 0.14, "w": 0.52, "h": 0.15, "rot": -52,
      "color": "195,232,255", "base": 0.64, "amp": 0.34, "k": 2, "phase": 0.0 }
  ],

  // Big soft background glows — glowing city, sky, aura, fire haze.
  "auras": [
    { "cx": 0.53, "cy": 0.20, "w": 0.60, "h": 0.60, "blur": 44,
      "color": "255,206,124", "base": 0.40, "amp": 0.16, "k": 1, "phase": 0.0 }
  ],

  // Drifting motes — pyreflies, embers, dust, snow. Colors are "R,G,B" strings.
  "particles": { "count": 130, "palette": ["255,224,150","150,215,255","255,255,255"] },

  // OPT-IN, RISKY — localized soft-body wobble. Default to [] (none).
  "jiggle": []
}
```

- All positions are **normalized 0..1**. Use `calibrate_grid.sh` to read coordinates off a 10% grid.
- `color` fields are `"R,G,B"` (no `rgb()`), so the engine can vary alpha per frame.
- `base` = resting brightness, `amp` = pulse depth, `k` = pulses per loop, `phase` = offset so
  several glows don't pulse in unison (use spread values like 0, 1.6, 0.8, 2.2).

## Effect design notes

**Halos.** Elongate along the blade with `w`/`h` and `rot` (degrees). Cool blue-white
(`"170,220,255"`) reads as steel/magic; gold (`"255,206,120"`) as holy/energy. Keep `base+amp ≤ ~1.0`
or the core clips to flat white and the weapon detail disappears.

**Auras.** Big + very blurred (`blur` 40–70). These are what sell an environment vibe (a glowing
city, a spirit sky). Layer 2–3: a warm core, a second warm offset, and one large cool wash.

**Particles.** 40–60 = tasteful; 120–150 = lush. Bias the palette to the scene (gold+cyan for
spirits, orange for embers, white-blue for snow). They drift upward by default.

**Breathing.** Keep `amp` ≤ ~0.01 — it scales the whole frame; more than that looks like a zoom.

## Lessons learned the hard way (do not relearn these)

1. **`inset: 0` does NOT fill a div in Remotion's Chromium** — it collapses the image to a thin
   strip. Always use explicit `top/left:0; width:100%; height:100%`. (The engine already does this.)

2. **Jiggle is a trap on flat paintings.** A naive `scaleY` from the top edge *stretches* the
   region — breasts/cloth visibly elongate like rubber ("melting"), which looks grotesque. If you
   must do it: (a) tiny amplitude (`amp` ~0.015–0.02, `ty` ~3–4px), (b) **volume conservation**
   (the engine narrows X as it stretches Y → jelly wobble, not elongation), (c) a **tight** soft
   mask on just the mass so it doesn't drag neighbouring detail. Even then, many users find it
   cheap/creepy — offer it, default it OFF, and drop it the moment they wince.

3. **Verify motion with an amplified frame-difference**, not by staring at stills:
   `ffmpeg -i a.png -i b.png -filter_complex "[0][1]blend=all_mode=difference,eq=contrast=6" diff.png`
   — only the moving regions light up, so you can confirm effects are localized and nothing else warps.

4. **Never `-c:v copy` a high-bitrate loop into a long video without telling the user the size.**
   Remotion output of a detailed/grainy image runs ~20 Mbps → a 1-hour copy-loop is ~9 GB.
   Copy = max quality; re-encoding at 4–6 Mbps looks identical for near-static loops and is ~2 GB.

5. **mp3s often embed cover art as a second (video) stream.** Always `-map 1:a:0` when muxing so
   you grab audio, not the PNG. (assemble.sh does this.)

6. **Music copyright.** Game/film OSTs (e.g. "To Zanarkand") are almost always Content-ID matched
   on YouTube regardless of clip length. Warn the user; it's their call.
