# FlowState demo reel

A [Remotion](https://remotion.dev) project that renders the FlowState pitch to
an MP4: 14 scenes, 1920×1080 at 30fps, narrated by Gemini TTS.

Every scene is timed to its own narration clip rather than to a fixed length,
so cuts land on sentence ends instead of mid-word. Motion is derived from the
frame number through `spring()` and `interpolate()`, never from CSS animation,
which makes the render deterministic: frame 412 is identical on every machine
and on every re-render.

## Render it

```powershell
cd video
npm install
npm run render          # -> out/flowstate.mp4
```

`npm run studio` opens the Remotion editor if you want to scrub or tweak a
scene; `npm run still` writes a single frame as a PNG for a thumbnail.

## Regenerate the narration

The committed WAVs mean you can render without an API key. To change the
script, edit `narration.json` and re-run:

```powershell
$env:GEMINI_API_KEY = '<your key>'
powershell -File make-narration.ps1
```

The key is read from the environment and never written to a file. Clips are
cached, so a re-run only generates what is missing; pass `-Force` to redo
everything, or `-Voice Puck` / `-Model gemini-3.8-flash-lite-tts` to change
either.

### On quota

TTS quota is enforced **per model**. A long script can exhaust one model
mid-run and return 429 no matter how long you back off, while a different
model answers immediately. Clips are therefore stored under
`public/audio/<model>/`, so switching models never leaves you with a reel
whose voice changes timbre halfway through, and a partial set survives to be
resumed. `narration.json` names the model the committed clips came from.

## Layout

    narration.json          the script: one entry per scene, with delivery direction
    make-narration.ps1      calls Gemini TTS, writes clips + public/audio/manifest.json
    src/theme.ts            palette, fonts, frame rate, scene tail padding
    src/ui.tsx              motion hooks and the brutalist primitives
    src/scenes.tsx          all 14 scenes
    src/Reel.tsx            sequences them, timing each to its clip
    src/Root.tsx            the composition

## Changing the numbers

Figures on screen are typed into `src/scenes.tsx` by hand. They come from
`python -m flowstate.detect`, `python -m flowstate.detect_naive` and
`scripts/demo_compare.sh` in the parent project. If those change, the scenes
that quote them are `relative`, `counts`, `rubric`, `skill`, `ranking` and
`onchain` — and `narration.json` repeats several of the same figures aloud, so
update both or the voice will contradict the picture.
