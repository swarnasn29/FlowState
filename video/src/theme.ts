import { loadFont as loadDisplay } from "@remotion/google-fonts/ArchivoBlack";
import { loadFont as loadMono } from "@remotion/google-fonts/SpaceMono";

// Both loaders register a delayRender internally, so the headless browser
// never screenshots a frame with a fallback face substituted in.
export const display = loadDisplay().fontFamily;
export const mono = loadMono().fontFamily;

/** Maximalist brutalism: raw concrete, hard ink, three clashing saturated accents. */
export const C = {
  ink: "#0a0a0a",
  concrete: "#dcdad3",
  slab: "#c9c6bd",
  paper: "#f2f0eb",
  blue: "#1f3cff",
  acid: "#d8ff00",
  siren: "#ff3b1f",
  dim: "#6d6a63",
} as const;

export const FPS = 30;

/** Frames of air left after a narration line finishes, so cuts never clip speech. */
export const TAIL = 22;

/** Used when a scene has no narration clip yet. */
export const FALLBACK_FRAMES = 7 * FPS;
