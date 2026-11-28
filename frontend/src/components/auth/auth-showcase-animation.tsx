"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";

import { ClipCutAnimation } from "@/components/auth/clip-cut-animation";
import { StoryBuildAnimation } from "@/components/auth/story-build-animation";
import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";

type Mode = "story" | "clip";

const CYCLE_MS = 8400;

export function AuthShowcaseAnimation() {
  const reduceMotion = usePrefersReducedMotion();
  const [mode, setMode] = useState<Mode>("story");

  useEffect(() => {
    if (reduceMotion) return;
    const timer = window.setInterval(() => {
      setMode((prev) => (prev === "story" ? "clip" : "story"));
    }, CYCLE_MS);
    return () => window.clearInterval(timer);
  }, [reduceMotion]);

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={mode}
        initial={reduceMotion ? false : { opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        exit={reduceMotion ? undefined : { opacity: 0, y: -6 }}
        transition={{ duration: 0.3 }}
      >
        {mode === "story" ? <StoryBuildAnimation /> : <ClipCutAnimation />}
      </motion.div>
    </AnimatePresence>
  );
}
