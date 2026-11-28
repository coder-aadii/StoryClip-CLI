"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";

import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";

const PHASES = {
  prompt: 0,
  scenes: 1700,
  voice: 3000,
  video: 4600,
  fade: 7600,
  restart: 8200,
} as const;
type Phase = keyof typeof PHASES;

const SCENE_X = [44, 184, 324];
const VOICE_BARS = Array.from({ length: 34 }, (_, i) => {
  const x = 86 + i * 9.2;
  const h = 4 + Math.abs(Math.sin(i * 0.85) * 9 + Math.cos(i * 0.45) * 5);
  return { x, y1: 224 - h, y2: 224 + h };
});

export function StoryBuildAnimation() {
  const reduceMotion = usePrefersReducedMotion();
  const [cycle, setCycle] = useState(0);
  const [phase, setPhase] = useState<Phase>("prompt");

  useEffect(() => {
    if (reduceMotion) return;
    const timers = (Object.keys(PHASES) as Phase[]).map((name) =>
      window.setTimeout(() => {
        if (name === "restart") {
          setPhase("prompt");
          setCycle((value) => value + 1);
        } else {
          setPhase(name);
        }
      }, PHASES[name]),
    );
    return () => timers.forEach(window.clearTimeout);
  }, [cycle, reduceMotion]);

  const shown: Phase = reduceMotion ? "video" : phase;
  const at = (name: Phase) => PHASES[shown] >= PHASES[name];
  const fading = !reduceMotion && shown === "fade";

  return (
    <div className="mx-auto w-full max-w-[460px] select-none text-white" aria-hidden="true">
      <svg viewBox="0 0 480 336" fill="none" className="h-auto w-full overflow-visible">
        <motion.g
          key={cycle}
          initial={reduceMotion ? false : { opacity: 0 }}
          animate={{ opacity: fading ? 0 : 1 }}
          transition={{ duration: 0.45 }}
        >
          {/* Step 1: Idea / Script prompt box */}
          <rect
            x={40}
            y={20}
            width={400}
            height={58}
            rx={12}
            stroke="currentColor"
            strokeOpacity={0.35}
            strokeWidth={1.25}
          />
          <circle cx={60} cy={40} r={3} fill="currentColor" fillOpacity={0.55} />
          <motion.line
            x1={74}
            y1={40}
            x2={290}
            y2={40}
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            initial={{ pathLength: reduceMotion ? 1 : 0, opacity: 0.85 }}
            animate={{ pathLength: 1, opacity: 0.85 }}
            transition={{ duration: 0.9, ease: "easeOut" }}
          />
          <motion.line
            x1={74}
            y1={56}
            x2={215}
            y2={56}
            stroke="currentColor"
            strokeWidth={1.75}
            strokeLinecap="round"
            initial={{ pathLength: reduceMotion ? 1 : 0, opacity: 0.45 }}
            animate={{ pathLength: 1, opacity: 0.45 }}
            transition={{ duration: 0.7, delay: 0.55, ease: "easeOut" }}
          />

          {/* Step 2: Storyboard scene cards + Voice waveform */}
          <AnimatePresence>
            {at("scenes") && !at("video") && (
              <motion.g
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.96 }}
                transition={{ duration: 0.35 }}
              >
                {SCENE_X.map((x, idx) => (
                  <motion.g
                    key={x}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.35, delay: idx * 0.12 }}
                  >
                    <rect
                      x={x}
                      y={104}
                      width={112}
                      height={88}
                      rx={10}
                      stroke="currentColor"
                      strokeOpacity={0.45}
                      strokeWidth={1.25}
                    />
                    <rect
                      x={x + 10}
                      y={114}
                      width={92}
                      height={44}
                      rx={6}
                      stroke="currentColor"
                      strokeOpacity={0.25}
                      strokeWidth={1}
                    />
                    <path
                      d={`M${x + 18} 150 L${x + 42} 130 L${x + 62} 144 L${x + 76} 134 L${x + 94} 150`}
                      stroke="currentColor"
                      strokeOpacity={0.5}
                      strokeWidth={1.25}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                    <line
                      x1={x + 12}
                      y1={170}
                      x2={x + 88}
                      y2={170}
                      stroke="currentColor"
                      strokeOpacity={0.6}
                      strokeWidth={1.75}
                      strokeLinecap="round"
                    />
                    <line
                      x1={x + 12}
                      y1={180}
                      x2={x + 64}
                      y2={180}
                      stroke="currentColor"
                      strokeOpacity={0.35}
                      strokeWidth={1.5}
                      strokeLinecap="round"
                    />
                  </motion.g>
                ))}

                {at("voice") && (
                  <motion.g initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}>
                    {VOICE_BARS.map((bar, i) => (
                      <motion.line
                        key={bar.x}
                        x1={bar.x}
                        y1={bar.y1}
                        x2={bar.x}
                        y2={bar.y2}
                        stroke="currentColor"
                        strokeOpacity={0.6}
                        strokeWidth={2}
                        strokeLinecap="round"
                        initial={{ scaleY: 0.3 }}
                        animate={{ scaleY: [0.5, 1.15, 0.7] }}
                        transition={{
                          duration: 0.9,
                          delay: i * 0.02,
                          repeat: Infinity,
                          repeatType: "reverse",
                        }}
                      />
                    ))}
                    <text
                      x={240}
                      y={260}
                      textAnchor="middle"
                      fill="currentColor"
                      fillOpacity={0.55}
                      fontSize={11}
                      fontFamily="ui-monospace, monospace"
                    >
                      scenes · voiceover · visuals
                    </text>
                  </motion.g>
                )}
              </motion.g>
            )}
          </AnimatePresence>

          {/* Step 3: Rendered video player + subtitles */}
          {at("video") && (
            <motion.g
              initial={reduceMotion ? false : { opacity: 0, scale: 0.95, y: 8 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              transition={{ duration: 0.45, ease: "easeOut" }}
            >
              <rect
                x={92}
                y={104}
                width={296}
                height={176}
                rx={14}
                stroke="currentColor"
                strokeOpacity={0.7}
                strokeWidth={1.5}
              />
              <circle
                cx={240}
                cy={176}
                r={22}
                stroke="currentColor"
                strokeOpacity={0.6}
                strokeWidth={1.4}
              />
              <path
                d="M235 167 L251 176 L235 185 Z"
                fill="currentColor"
                fillOpacity={0.8}
              />
              {/* Animated subtitle bars */}
              <motion.line
                x1={164}
                y1={232}
                x2={316}
                y2={232}
                stroke="currentColor"
                strokeOpacity={0.85}
                strokeWidth={2.5}
                strokeLinecap="round"
                initial={{ pathLength: reduceMotion ? 1 : 0 }}
                animate={{ pathLength: 1 }}
                transition={{ duration: 0.6, delay: 0.2 }}
              />
              <line
                x1={120}
                y1={258}
                x2={360}
                y2={258}
                stroke="currentColor"
                strokeOpacity={0.25}
                strokeWidth={2}
                strokeLinecap="round"
              />
              <motion.line
                x1={120}
                y1={258}
                x2={275}
                y2={258}
                stroke="currentColor"
                strokeOpacity={0.85}
                strokeWidth={2}
                strokeLinecap="round"
                initial={{ pathLength: reduceMotion ? 1 : 0 }}
                animate={{ pathLength: 1 }}
                transition={{ duration: 1.8, ease: "easeInOut" }}
              />
              <text
                x={240}
                y={306}
                textAnchor="middle"
                fill="currentColor"
                fillOpacity={0.55}
                fontSize={11}
                fontFamily="ui-monospace, monospace"
              >
                narrated · subtitled · ready to export
              </text>
            </motion.g>
          )}
        </motion.g>
      </svg>
    </div>
  );
}
