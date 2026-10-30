import React from "react";
import { Composition } from "remotion";
import { Reel, SHORT_CUT, SHORT_FRAMES, SHORT_TAIL, TOTAL_FRAMES } from "./Reel";
import { FPS } from "./theme";

export const RemotionRoot: React.FC = () => (
  <>
    {/* The full reel: every feature, ~4:36. */}
    <Composition
      id="Reel"
      component={Reel}
      durationInFrames={TOTAL_FRAMES}
      fps={FPS}
      width={1920}
      height={1080}
      defaultProps={{}}
    />
    {/* The submission cut, under the 3-minute cap most forms impose. Same
        clips and same timing - scenes are dropped, never sped up, so the
        narration is never clipped. */}
    <Composition
      id="ReelShort"
      component={Reel}
      durationInFrames={SHORT_FRAMES}
      fps={FPS}
      width={1920}
      height={1080}
      defaultProps={{ only: SHORT_CUT, tail: SHORT_TAIL }}
    />
  </>
);
