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
export const sceneFrames = (id: string): number => {
  const clip = clips.get(id);
  if (!clip) return FALLBACK_FRAMES;
  return Math.ceil(clip.seconds * FPS) + TAIL;
};

export const TOTAL_FRAMES = SCENES.reduce(
  (total, scene) => total + sceneFrames(scene.id),
  0
);

export const Reel: React.FC = () => {
  let cursor = 0;
  return (
    <AbsoluteFill style={{ backgroundColor: C.concrete }}>
      {SCENES.map(({ id, component: Scene }) => {
        const duration = sceneFrames(id);
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
