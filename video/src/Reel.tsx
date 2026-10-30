import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import manifest from "../public/audio/manifest.json";
import { SCENES } from "./scenes";
import { C, FALLBACK_FRAMES, FPS, TAIL } from "./theme";

type Clip = { id: string; file: string; seconds: number };

const clips = new Map<string, Clip>(
  (manifest.scenes as Clip[]).map((clip) => [clip.id, clip])
);

/**
 * Each scene lasts exactly as long as its narration clip, plus a tail so a cut
 * never lands on the last syllable. Timing the picture to the voice rather
 * than the other way round is what keeps the reel from feeling read-over.
 */
export const sceneFrames = (id: string, tail: number = TAIL): number => {
  const clip = clips.get(id);
  if (!clip) return FALLBACK_FRAMES;
  return Math.ceil(clip.seconds * FPS) + tail;
};

/**
 * A tighter tail for the submission cut. At the default 22 frames the nine
 * surviving scenes total 3:01, and a form that says "3 minutes" may well mean
 * it. Half a second after a sentence ends still does not clip speech, and it
 * buys the twelve seconds of headroom that matter.
 */
export const SHORT_TAIL = 15;

export const TOTAL_FRAMES = SCENES.reduce(
  (total, scene) => total + sceneFrames(scene.id),
  0
);

/**
 * The scenes that survive a three-minute cut, in order.
 *
 * Dropped: `nolog` (restates `invisible`), `telemetry` (most technical, least
 * persuasive), `transport` (the v11 story belongs in a write-up, not a pitch),
 * `counts` and `rubric` (the `skill` scene's numbers ARE rubric scores, so it
 * carries them implicitly).
 *
 * `relative` is kept over `rubric` deliberately: it is the only scene that
 * shows real telemetry, it is where the problem visibly becomes hard - the
 * broken step is faster than the healthy one - and it motivates the 0.370 bar
 * that reappears in `skill`. A rubric is told; this is shown.
 */
export const SHORT_CUT = [
  "title",
  "invisible",
  "kinds",
  "relative",
  "streams",
  "skill",
  "ranking",
  "onchain",
  "close",
] as const;

export const SHORT_FRAMES = SHORT_CUT.reduce(
  (total, id) => total + sceneFrames(id, SHORT_TAIL),
  0
);

export const Reel: React.FC<{ only?: readonly string[]; tail?: number }> = ({
  only,
  tail = TAIL,
}) => {
  const running = only ? SCENES.filter((s) => only.includes(s.id)) : SCENES;
  let cursor = 0;
  return (
    <AbsoluteFill style={{ backgroundColor: C.concrete }}>
      {running.map(({ id, component: Scene }) => {
        const duration = sceneFrames(id, tail);
        const from = cursor;
        cursor += duration;
        const clip = clips.get(id);
        return (
          <Sequence key={id} from={from} durationInFrames={duration} name={id}>
            <Scene />
            {clip ? <Audio src={staticFile(clip.file)} /> : null}
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
