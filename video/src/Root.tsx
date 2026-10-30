import React from "react";
import { Composition } from "remotion";
import { Reel, TOTAL_FRAMES } from "./Reel";
import { FPS } from "./theme";

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Reel"
    component={Reel}
    durationInFrames={TOTAL_FRAMES}
    fps={FPS}
    width={1920}
    height={1080}
  />
);
