---
name: simple-image-video
description: >-
  Animate ONE supplied still image into a long, seamlessly-looping video with music — e.g. a game
  key-art / poster / illustration turned into a 1-hour ambient loop for YouTube. Adds tasteful
  motion: pulsing weapon/light halos, glowing background auras (spirit-city / sky vibe), drifting
  particles (pyreflies, embers, dust, snow), gentle breathing, and a mathematically seamless loop.
  Use whenever the user hands you an image and wants to "make it move", "bring it to life",
  "animate this picture", "make a looping video / live wallpaper / 1-hour music loop / lofi
  background" from a static image plus a song — even if they don't say the word "loop". NOT for
  generating video from a text prompt (that's AI video-gen) and NOT for editing existing footage.
---

# Simple Image → Looping Video

Turn a single still image + a music track into a long, seamless looping video. The motion is added
as layered, sine-driven effects (glows, auras, particles, breathing) rendered with Remotion, then
the short seamless loop is repeated to fill the target length and muxed with the music.

**Why this approach:** every effect is a sine wave with an integer number of cycles per loop, so
the first and last frame are identical — the loop is *mathematically* seamless (no seam, no
crossfade), which is exactly what a YouTube/wallpaper loop needs. See
`references/effects-and-lessons.md` for the full theory, props schema, and mistakes to avoid.

## Step 0 — Ask the options FIRST (do not skip)

The user's taste drives everything here, and some choices are expensive to redo. Before building,
ask (use the question tool, offer sensible defaults, let them override):

1. **Which effects?** Offer the menu and let them pick any combination:
   - **Halos** — pulsing glow on weapons / lights / gems / eyes.
   - **Auras / vibe** — big soft background glows (glowing city, sky, magical aura, fire haze).
   - **Particles** — drifting motes (pyreflies, embers, dust, snow).
   - **Breathing** — subtle whole-frame life.
   - **Jiggle** — localized soft-body wobble. **Default OFF.** It looks cheap/creepy on flat art
     and users often recoil — only add if explicitly asked, and read the jiggle warning in the
     reference before touching it.
2. **Vibe / theme** (one line) — e.g. "ethereal Zanarkand spirit vibe", "cozy lofi", "epic".
3. **Output bitrate** — **default 20 Mbps (max quality)**. Ask whether they want to **drop it
   (e.g. 4–6 Mbps) to save disk / upload faster**. Explain the tradeoff honestly: for a
   near-static loop, 4–6 Mbps looks essentially identical but a 1-hour file drops from ~9 GB to
   ~2 GB. Do NOT silently pick low quality, and do NOT silently ship a 9 GB file.
4. **Final length** — default ~1 hour (a looping music video). Also fine: full-song length, or a
   short single-loop clip.
5. **Loop clip length** — default 7 s (`durationInFrames = fps × seconds`). Longer = dreamier.

Also confirm the two inputs up front: the **image path** and the **music file path** (or offer to
download audio from a URL the user supplies).

## Step 1 — Set up the render project

```bash
SKILL="$(dirname "$0")"   # this skill's directory
WORK=projects/<name>/_remotion
bash "$SKILL/scripts/setup_remotion.sh" "$SKILL" "$WORK"
cp <the-image> "$WORK/public/img.png"
```

`setup_remotion.sh` copies the bundled `remotion-template/` and runs `npm install` once (needs
Node ≥ 18 and `npx`). One composition, `ImageLoop`, renders any image at any aspect / loop length —
everything is driven by a props JSON.

## Step 2 — Calibrate effect positions on a 10% grid

Effects are placed with **normalized 0..1 coordinates**. Overlay a grid to read them off precisely:

```bash
bash "$SKILL/scripts/calibrate_grid.sh" <the-image> projects/<name>/grid.png
```

Read the grid image. Each cell = 10%. A target's center is `(col/10, row/10)`. Note the pixel
coordinates of every weapon/light (for halos), background glow source (auras), etc.

## Step 3 — Write the props and render a short loop

Author `projects/<name>/props.json` following the schema in `references/effects-and-lessons.md`
(copy `references/example-props.json` as a starting point). Set `width`/`height` to the image's
aspect, `durationInFrames` to the chosen loop length, and fill the effect arrays from your Step-2
coordinates and the chosen vibe.

Render the short loop:

```bash
cd "$WORK"
npx remotion render src/index.tsx ImageLoop ../loop.mp4 --props=../props.json
```

Tip: render a single still first to check placement fast:
`npx remotion still src/index.tsx ImageLoop ../calib.png --frame=20 --props=../props.json`

## Step 4 — Verify the motion, then iterate with the user

Do NOT judge motion from one still. Confirm effects are localized and nothing warps unexpectedly
with an **amplified frame-difference** (moving regions light up, everything static stays black):

```bash
ffmpeg -y -i f_a.png -i f_b.png -filter_complex "[0][1]blend=all_mode=difference,eq=contrast=6" diff.png
```

Then **open the loop for the user** (`open loop.mp4`) and iterate on the props — intensity, colors,
speed, positions — until they're happy. Re-rendering the short loop is fast and free. This is the
creative core; expect a few rounds.

## Step 5 — Assemble the final long video + music

Once the loop is approved, repeat it to the target length and mux the music at the chosen bitrate:

```bash
# copy = max quality (big file);  a number = re-encode at that many Mbps
bash "$SKILL/scripts/assemble.sh" projects/<name>/loop.mp4 <music-file> \
     projects/<name>/final.mp4 <seconds> <20|copy|5>
```

`assemble.sh` loops both video and audio, maps the correct audio stream (mp3 cover-art safe),
targets the exact duration, and adds `+faststart` for streaming. Report the final path and size.

## Step 6 — Deliver honestly

- State the output path, duration, resolution, and **file size** plainly.
- If you used max-quality/copy and the file is large, remind them a re-encode shrinks it a lot.
- **Copyright:** if the music is a game/film OST, warn that YouTube Content-ID will likely claim it
  regardless of length — it won't block uploading, but may add ads or region-block. Their call.

## Reference

- `references/effects-and-lessons.md` — seamless-loop theory, full props schema, effect design
  notes, and the mistakes already paid for (Remotion `inset:0` bug, the jiggle "melting" trap,
  frame-diff verification, copy-loop file-size blowup, mp3 cover-art stream). **Read it before
  writing props.**
- `references/example-props.json` — a filled-in "glowing spirit-city" props file to copy from.
