import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { C, display, mono } from "./theme";
import {
  Bar,
  Cap,
  Eyebrow,
  Headline,
  Marquee,
  Num,
  Row,
  Rule,
  Slab,
  SlabTitle,
  Stage,
  Stamp,
  Tick,
  useFlip,
  useFly,
} from "./ui";

/* ---------------------------------------------------------------- 1. title */
export const Title: React.FC = () => (
  <Stage>
    <Eyebrow>Bittensor · Protocol Track · netuid 600</Eyebrow>
    <div style={{ display: "flex", flexDirection: "column", gap: 30 }}>
      <Headline size={248}>FlowState</Headline>
      <Rule delay={14} />
      <div style={{ ...useFly(18), fontSize: 46, fontWeight: 700, maxWidth: 1240, lineHeight: 1.3 }}>
        Finds the exact step where a Web3 user hit friction.
      </div>
    </div>
    <Row>
      <Slab tone="acid" delay={26}>
        <Num>600</Num>
        <Cap>netuid, test.finney</Cap>
      </Slab>
      <Slab tone="blue" delay={31}>
        <Num>3</Num>
        <Cap>neurons registered and serving</Cap>
      </Slab>
      <Slab tone="paper" delay={36}>
        <Num>τ1.017</Num>
        <Cap>total spend, subnet to weights</Cap>
      </Slab>
    </Row>
    <Marquee text="one weekend · one designer · no prior backend · weights live on chain" />
  </Stage>
);

/* ------------------------------------------------------------ 2. invisible */
export const Invisible: React.FC = () => {
  const deg = useFlip(10, 46);
  const face: React.CSSProperties = {
    position: "absolute",
    inset: 0,
    backfaceVisibility: "hidden",
    border: `9px solid ${C.ink}`,
    boxShadow: `17px 17px 0 ${C.ink}`,
    padding: 46,
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    gap: 18,
  };
  return (
    <Stage>
      <Eyebrow color={C.blue}>The invisible failure</Eyebrow>
      <Row gap={34}>
        <div style={{ ...useFly(6), flex: 1, perspective: 1600, height: 440 }}>
          <div
            style={{
              position: "relative",
              width: "100%",
              height: "100%",
              transformStyle: "preserve-3d",
              transform: `rotateY(${deg}deg)`,
            }}
          >
            <div style={{ ...face, background: C.paper }}>
              <div style={{ fontWeight: 700, fontSize: 25, letterSpacing: "0.3em" }}>
                WHAT THE USER SEES
              </div>
              <div style={{ fontFamily: display, fontSize: 118, lineHeight: 0.95 }}>pending</div>
              <Cap size={32}>for 24.6 seconds, then nothing</Cap>
            </div>
            <div
              style={{
                ...face,
                background: C.blue,
                color: C.paper,
                transform: "rotateY(180deg)",
              }}
            >
              <div style={{ fontWeight: 700, fontSize: 25, letterSpacing: "0.3em", color: C.acid }}>
                WHAT THE CHAIN RECORDED
              </div>
              <div style={{ fontFamily: display, fontSize: 118, lineHeight: 0.95 }}>confirmed</div>
              <Cap size={32}>already settled, 0.4 seconds in</Cap>
            </div>
          </div>
        </div>
        <Slab tone="siren" delay={60} flex={0.85}>
          <SlabTitle>The tokens were there</SlabTitle>
          <Cap>
            The whole time the user sat watching a spinner. Most people refresh, or submit the swap a
            second time and pay gas twice.
          </Cap>
        </Slab>
      </Row>
      <Rule delay={70} />
      <Marquee text="ui pending · chain confirmed · ui pending · chain confirmed" />
    </Stage>
  );
};

/* ---------------------------------------------------------------- 3. nolog */
export const NoLog: React.FC = () => (
  <Stage bg={C.ink} fg={C.paper}>
    <Eyebrow color={C.acid}>And nothing was recorded</Eyebrow>
    <Headline size={168} color={C.acid} width={1620}>
      No error fired
    </Headline>
    <div style={{ ...useFly(14), fontSize: 46, lineHeight: 1.3, maxWidth: 1500 }}>
      So nothing was logged. Every analytics tool on the market saw a perfectly healthy session.
    </div>
    <Row>
      <Slab tone="paper" delay={22}>
        <Num size={74}>0</Num>
        <Cap>errors thrown</Cap>
      </Slab>
      <Slab tone="paper" delay={26}>
        <Num size={74}>0</Num>
        <Cap>rage clicks</Cap>
      </Slab>
      <Slab tone="paper" delay={30}>
        <Num size={74}>0</Num>
        <Cap>alerts raised</Cap>
      </Slab>
      <Slab tone="acid" delay={34}>
        <Num size={74}>1</Num>
        <Cap>user who will not come back</Cap>
      </Slab>
    </Row>
  </Stage>
);

/* ------------------------------------------------------------ 4. telemetry */
export const Telemetry: React.FC = () => (
  <Stage>
    <Eyebrow color={C.blue}>One event, both halves</Eyebrow>
    <Headline size={92} width={1640}>
      Read the UI and the chain as one timeline
    </Headline>
    <Row gap={28}>
      <Slab tone="paper" delay={8} motion="spin">
        <SlabTitle size={36}>UI side</SlabTitle>
        <Cap>step · dwell_ms · retries · rage_clicks · ui_state · errors[]</Cap>
        <Num size={56}>pending</Num>
      </Slab>
      <Slab tone="blue" delay={14} motion="spin">
        <SlabTitle size={36}>Chain side</SlabTitle>
        <Cap>tx_hash · status, on the three steps that await settlement</Cap>
        <Num size={56}>confirmed</Num>
      </Slab>
      <Slab tone="acid" delay={20} motion="spin">
        <SlabTitle size={36}>The tell</SlabTitle>
        <Cap>
          No benign session makes this. A real wait reports pending on both sides; a failure reports
          reverted on both.
        </Cap>
        <Num size={56}>desync</Num>
      </Slab>
    </Row>
    <Marquee text="a session replay tool never sees the second column" />
  </Stage>
);

/* ---------------------------------------------------------------- 5. kinds */
const KindRow: React.FC<{
  name: string;
  what: string;
  tag: string;
  delay: number;
  tone?: "paper" | "siren";
  tagTone?: string;
  tagFg?: string;
}> = ({ name, what, tag, delay, tone = "paper", tagTone, tagFg }) => (
  <Slab tone={tone} delay={delay} row>
    <div style={{ fontFamily: display, fontSize: 48, width: 470, textTransform: "uppercase" }}>
      {name}
    </div>
    <div style={{ flex: 1, fontSize: 29 }}>{what}</div>
    <Tick tone={tagTone} fg={tagFg}>
      {tag}
    </Tick>
  </Slab>
);

export const Kinds: React.FC = () => (
  <Stage>
    <Eyebrow color={C.blue}>Four friction types</Eyebrow>
    <div style={{ display: "flex", flexDirection: "column", gap: 20, flex: 1, justifyContent: "center" }}>
      <KindRow name="stall" what="a spinner that never resolves" tag="UI sees it" delay={4} />
      <KindRow name="error_loop" what="the approval or slippage trap, repeatedly" tag="UI sees it" delay={10} />
      <KindRow name="rage_click" what="the button looks dead, so they hammer it" tag="UI sees it" delay={16} />
      <KindRow
        name="silent_failure"
        what="it worked — the UI never said so"
        tag="needs the chain"
        delay={24}
        tone="siren"
        tagTone={C.ink}
        tagFg={C.acid}
      />
    </div>
    <Marquee text="three visible · one invisible · the invisible one costs the most" />
  </Stage>
);

/* --------------------------------------------------------------- 6. counts */
export const Counts: React.FC = () => (
  <Stage>
    <Eyebrow color={C.blue}>Zero, one, or two</Eyebrow>
    <Headline size={88} width={1640}>
      Most sessions are clean. Saying so is the right answer.
    </Headline>
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      <Bar label="0 frictions — clean" value="30.8%" pct={30.8} tone={C.acid} delay={10} />
      <Bar label="1 friction" value="52.3%" pct={52.3} tone={C.blue} delay={16} />
      <Bar label="2 frictions" value="16.9%" pct={16.9} tone={C.siren} delay={22} />
    </div>
    <Row>
      <Slab tone="paper" delay={30}>
        <SlabTitle size={34}>No freebie left</SlabTitle>
        <Cap>
          When every session held exactly one friction, a miner named its best guess and was right
          almost always.
        </Cap>
      </Slab>
      <Slab tone="acid" delay={36}>
        <SlabTitle size={34}>Silence scores 1.00</SlabTitle>
        <Cap>A clean session answered with an empty set earns full marks. A confident false positive earns 0.02.</Cap>
      </Slab>
    </Row>
    <Stamp delay={44}>harder</Stamp>
  </Stage>
);

/* ------------------------------------------------------------- 7. relative */
export const Relative: React.FC = () => {
  const frame = useCurrentFrame();
  const cell: React.CSSProperties = {
    border: `6px solid ${C.ink}`,
    padding: "18px 22px",
    fontSize: 30,
  };
  const head: React.CSSProperties = {
    ...cell,
    background: C.ink,
    color: C.acid,
    fontWeight: 700,
    fontSize: 24,
    letterSpacing: "0.14em",
    textTransform: "uppercase",
  };
  const reveal = interpolate(frame - 14, [0, 14], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <Stage>
      <Eyebrow color={C.blue}>Slow is not broken</Eyebrow>
      <Headline size={88} width={1660}>
        The broken step is faster than the healthy one
      </Headline>
      <table style={{ ...useFly(8), borderCollapse: "collapse", width: "100%", fontFamily: mono }}>
        <tbody>
          <tr>
            <th style={{ ...head, textAlign: "left" }}>step</th>
            <th style={{ ...head, textAlign: "right" }}>dwell</th>
            <th style={{ ...head, textAlign: "left" }}>ui</th>
            <th style={{ ...head, textAlign: "left" }}>chain</th>
            <th style={{ ...head, textAlign: "left" }}>verdict</th>
          </tr>
          <tr>
            <td style={cell}>tx_pending</td>
            <td style={{ ...cell, textAlign: "right", fontFamily: display }}>33,221 ms</td>
            <td style={cell}>ok</td>
            <td style={cell}>confirmed</td>
            <td style={cell}>healthy — congested chain</td>
          </tr>
          <tr style={{ background: `rgba(216,255,0,${reveal})` }}>
            <td style={{ ...cell, fontWeight: 700 }}>token_approve</td>
            <td style={{ ...cell, textAlign: "right", fontFamily: display }}>29,672 ms</td>
            <td style={{ ...cell, fontWeight: 700 }}>pending</td>
            <td style={{ ...cell, fontWeight: 700 }}>confirmed</td>
            <td style={{ ...cell, fontWeight: 700 }}>BROKEN — desync</td>
          </tr>
        </tbody>
      </table>
      <Row>
        <Slab tone="blue" delay={30}>
          <SlabTitle size={34}>Per-step norms</SlabTitle>
          <Cap>quote_received is normal at 0.3–1.2s. tx_pending is normal to 45s. Two orders of magnitude apart.</Cap>
        </Slab>
        <Slab tone="siren" delay={36}>
          <SlabTitle size={34}>“Slowest step” = 0.370</SlabTitle>
          <Cap>Below a miner with no history at all. One global threshold cannot survive a step that is legitimately slow.</Cap>
        </Slab>
      </Row>
    </Stage>
  );
};

/* ------------------------------------------------------------ 8. transport */
export const Transport: React.FC = () => (
  <Stage bg={C.ink} fg={C.paper}>
    <Eyebrow color={C.acid}>We wrote the transport</Eyebrow>
    <Headline size={112} color={C.acid} width={1660}>
      v11 deleted axon, dendrite and Synapse
    </Headline>
    <Row gap={22}>
      <Slab tone="acid" delay={8}>
        <Num size={88}>7/7</Num>
        <Cap>auth cases passing</Cap>
      </Slab>
      <Slab tone="paper" delay={13}>
        <SlabTitle size={27}>signature · receiver · tamper</SlabTitle>
        <Cap size={24}>a signature issued for one miner cannot be replayed at another</Cap>
      </Slab>
      <Slab tone="paper" delay={18}>
        <SlabTitle size={27}>replay · nonce window</SlabTitle>
        <Cap size={24}>the same signed request twice is refused</Cap>
      </Slab>
      <Slab tone="paper" delay={23}>
        <SlabTitle size={27}>registered · validator permit</SlabTitle>
        <Cap size={24}>a verified identity is not permission</Cap>
      </Slab>
    </Row>
    <Slab tone="blue" delay={32}>
      <SlabTitle size={34}>Every tutorial online is for v10</SlabTitle>
      <Cap>
        Written from the migration notes: FastAPI on the miner, httpx on the validator, bt.http_auth
        sign and verify, raw body hashed before parsing.
      </Cap>
    </Slab>
  </Stage>
);

/* -------------------------------------------------------------- 9. streams */
export const Streams: React.FC = () => (
  <Stage>
    <Eyebrow color={C.blue}>Scoring the unverifiable</Eyebrow>
    <Headline size={146} width={1680}>
      Real telemetry has no answer key
    </Headline>
    <Row gap={30}>
      <Slab tone="blue" delay={12} motion="spin">
        <Tick tone={C.acid}>the exam</Tick>
        <SlabTitle>Scored stream</SlabTitle>
        <Cap>
          The validator generates it. Friction at a known index. The answer never leaves the
          validator. <b>This alone sets weights.</b>
        </Cap>
      </Slab>
      <Slab tone="acid" delay={20} motion="spin">
        <Tick tone={C.ink} fg={C.acid}>
          the job
        </Tick>
        <SlabTitle>Organic stream</SlabTitle>
        <Cap>
          A real dApp session. No known answer. The report goes back. <b>Never scored.</b> This is
          the product.
        </Cap>
      </Slab>
    </Row>
    <Slab tone="paper" delay={30}>
      <SlabTitle size={34}>Byte-identical on the wire</SlabTitle>
      <Cap>
        A miner cannot tell which stream a request came from, so it cannot work hard only when it is
        being marked. Passing the exam is how it earns the right to serve real traffic.
      </Cap>
    </Slab>
  </Stage>
);

/* -------------------------------------------------------------- 10. rubric */
export const Rubric: React.FC = () => (
  <Stage>
    <Eyebrow color={C.blue}>The rubric</Eyebrow>
    <Headline size={96} width={1660}>
      Four criteria, printed every run
    </Headline>
    <Row gap={22}>
      <Slab tone="acid" delay={6}>
        <Num size={80}>0.70</Num>
        <SlabTitle size={30}>localisation</SlabTitle>
        <Cap size={24}>F1 over reported indices — the right steps, and only those</Cap>
      </Slab>
      <Slab tone="blue" delay={12}>
        <Num size={80}>0.20</Num>
        <SlabTitle size={30}>kind</SlabTitle>
        <Cap size={24}>of the steps located, the fraction labelled correctly</Cap>
      </Slab>
      <Slab tone="paper" delay={18}>
        <Num size={80}>0.10</Num>
        <SlabTitle size={30}>calibration</SlabTitle>
        <Cap size={24}>1 − Brier over its own confidences</Cap>
      </Slab>
      <Slab tone="siren" delay={24}>
        <Num size={80}>gate</Num>
        <SlabTitle size={30}>latency</SlabTitle>
        <Cap size={24}>no answer in time scores zero for the session</Cap>
      </Slab>
    </Row>
    <Slab tone="paper" delay={34}>
      <SlabTitle size={34}>Spraying cannot pay</SlabTitle>
      <Cap>
        Four guesses at two frictions scores 0.64. Honestly finding one of them scores 0.76. A hedged
        false positive earns five times a confident one.
      </Cap>
    </Slab>
    <Marquee text="a scoring rule miners cannot inspect is one they reverse engineer" />
  </Stage>
);

/* --------------------------------------------------------------- 11. skill */
export const Skill: React.FC = () => (
  <Stage>
    <Eyebrow color={C.blue}>2,000 unseen sessions · measured every run</Eyebrow>
    <Headline size={92} width={1660}>
      Does the task require skill?
    </Headline>
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Bar label="per-step profile, calibrated" value="0.846" pct={84.6} tone={C.acid} delay={6} labelWidth={620} />
      <Bar label="naive global thresholds" value="0.807" pct={80.7} tone={C.blue} delay={11} labelWidth={620} />
      <Bar label="same code, no history" value="0.524" pct={52.4} tone={C.ink} delay={16} labelWidth={620} />
      <Bar label="cheat: every non-ok ui" value="0.439" pct={43.9} tone={C.siren} delay={21} labelWidth={620} />
      <Bar label="cheat: the slowest step" value="0.370" pct={37.0} tone={C.siren} delay={26} labelWidth={620} />
      <Bar label="cheat: claim nothing, ever" value="0.280" pct={28.0} tone={C.dim} delay={31} labelWidth={620} />
    </div>
    <Slab tone="acid" delay={40}>
      <SlabTitle size={34}>Emission floor 0.70</SlabTitle>
      <Cap>
        Above every zero-effort strategy above, so a cheat and an untrained miner both receive
        nothing. The baselines are re-measured on every run, so a leaked label shows up instead of
        inflating the score.
      </Cap>
    </Slab>
  </Stage>
);

/* ------------------------------------------------------------- 12. ranking */
export const Ranking: React.FC = () => {
  const cell: React.CSSProperties = { border: `6px solid ${C.ink}`, padding: "18px 22px", fontSize: 30 };
  const head: React.CSSProperties = {
    ...cell,
    background: C.ink,
    color: C.acid,
    fontWeight: 700,
    fontSize: 23,
    letterSpacing: "0.12em",
    textTransform: "uppercase",
  };
  const n: React.CSSProperties = { ...cell, textAlign: "right", fontFamily: display };
  return (
    <Stage>
      <Eyebrow color={C.blue}>600 scored challenges each</Eyebrow>
      <Headline size={88} width={1680}>
        A subnet whose miners score alike ranks nobody
      </Headline>
      <table style={{ ...useFly(8), borderCollapse: "collapse", width: "100%", fontFamily: mono }}>
        <tbody>
          <tr>
            <th style={{ ...head, textAlign: "left" }}>miner</th>
            <th style={head}>uid</th>
            <th style={head}>score</th>
            <th style={head}>loc</th>
            <th style={head}>kind</th>
            <th style={head}>perfect</th>
            <th style={head}>emissions</th>
          </tr>
          <tr style={{ background: C.acid }}>
            <td style={{ ...cell, fontWeight: 700 }}>per-step profile</td>
            <td style={n}>1</td>
            <td style={n}>0.858</td>
            <td style={n}>0.87</td>
            <td style={n}>0.79</td>
            <td style={n}>81.3%</td>
            <td style={{ ...n, fontSize: 38 }}>59.1%</td>
          </tr>
          <tr>
            <td style={cell}>naive global</td>
            <td style={n}>2</td>
            <td style={n}>0.831</td>
            <td style={n}>0.83</td>
            <td style={n}>0.81</td>
            <td style={n}>76.7%</td>
            <td style={{ ...n, fontSize: 38 }}>40.9%</td>
          </tr>
        </tbody>
      </table>
      <Row>
        <Slab tone="blue" delay={26}>
          <SlabTitle size={34}>max(0, score − 0.70)²</SlabTitle>
          <Cap>Convex on purpose. A linear curve pays a materially better miner 4% more, so nobody improves and the subnet stagnates.</Cap>
        </Slab>
        <Slab tone="paper" delay={32}>
          <SlabTitle size={34}>Warm-up is real</SlabTitle>
          <Cap>Tied at 200 challenges. The learner only leads from 300. A subnet that rewards learned behaviour must tolerate new miners being briefly bad.</Cap>
        </Slab>
      </Row>
      <Stamp delay={40} tone={C.blue}>
        it ranks
      </Stamp>
    </Stage>
  );
};

/* ------------------------------------------------------------- 13. onchain */
export const OnChain: React.FC = () => (
  <Stage bg={C.ink} fg={C.paper}>
    <Eyebrow color={C.acid}>Not a mock · test.finney</Eyebrow>
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div style={{ fontWeight: 700, fontSize: 28, letterSpacing: "0.3em", color: C.dim }}>
        WEIGHTS COMMITTED, EXTRINSIC
      </div>
      <Headline size={252} color={C.acid}>
        8326769
      </Headline>
      <div style={{ ...useFly(16), fontSize: 44 }}>TimelockedWeightsCommitted · success=True</div>
    </div>
    <Row gap={22}>
      <Slab tone="acid" delay={24}>
        <SlabTitle size={34}>netuid 600</SlabTitle>
        <Cap>created, activated, named on chain as FlowState</Cap>
      </Slab>
      <Slab tone="blue" delay={29}>
        <SlabTitle size={34}>uid 0 · 1 · 2</SlabTitle>
        <Cap>discovered from the metagraph, nothing hardcoded</Cap>
      </Slab>
      <Slab tone="paper" delay={34}>
        <SlabTitle size={30}>{"{1: 0.587, 2: 0.413}"}</SlabTitle>
        <Cap>weights revealed, readable by anyone</Cap>
      </Slab>
    </Row>
    <Marquee text="registration cost τ0.008 not τ0.99 — we waited two hours for the burn to decay" />
  </Stage>
);

/* --------------------------------------------------------------- 14. close */
export const Close: React.FC = () => (
  <Stage>
    <Eyebrow color={C.blue}>What this does not claim</Eyebrow>
    <Row gap={28}>
      <Slab tone="siren" delay={6}>
        <SlabTitle size={36}>Not built</SlabTitle>
        <Cap>
          No ingestion endpoint — a dApp cannot send data yet, and organic sessions are simulated.
          Emissions are inert on testnet. One flow only.
        </Cap>
      </Slab>
      <Slab tone="paper" delay={12}>
        <SlabTitle size={36}>Found by testing my own work</SlabTitle>
        <Cap>
          A label leak — the friction tagged itself. Weights paid on luck from a five-sample average;
          it reached the chain. A loopback axon stored ip=0 and the miner vanished looking healthy.
        </Cap>
      </Slab>
    </Row>
    <Headline size={104} delay={22} width={1700}>
      The failure that costs the most doesn’t look like a failure
    </Headline>
    <div
      style={{
        ...useFly(34),
        alignSelf: "flex-start",
        background: C.acid,
        border: `9px solid ${C.ink}`,
        boxShadow: `17px 17px 0 ${C.ink}`,
        padding: "20px 34px",
        fontSize: 44,
        fontWeight: 700,
      }}
    >
      github.com/swarnasn29/FlowState
    </div>
    <Marquee text="flowstate · netuid 600 · test.finney · every number generated, not asserted" />
  </Stage>
);

export const SCENES: { id: string; component: React.FC }[] = [
  { id: "title", component: Title },
  { id: "invisible", component: Invisible },
  { id: "nolog", component: NoLog },
  { id: "telemetry", component: Telemetry },
  { id: "kinds", component: Kinds },
  { id: "counts", component: Counts },
  { id: "relative", component: Relative },
  { id: "transport", component: Transport },
  { id: "streams", component: Streams },
  { id: "rubric", component: Rubric },
  { id: "skill", component: Skill },
  { id: "ranking", component: Ranking },
  { id: "onchain", component: OnChain },
  { id: "close", component: Close },
];
