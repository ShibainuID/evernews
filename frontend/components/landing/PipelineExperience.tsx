"use client";

import { useRef } from "react";
import { AnimatePresence, motion } from "motion/react";
import { useTrailScroll } from "@/lib/useTrailScroll";
import { useIsMobile } from "@/lib/useIsMobile";
import { TrailCanvas } from "./TrailCanvas";

const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const;

const STAGES = [
  {
    kicker: "Step 1",
    title: "Take this taxi-and-train collision clip",
    body: "It's 14 seconds long. Instead of judging the whole thing at once, Evernews pulls five keyframes out of it, enough to carry the scene and small enough to search fast.",
  },
  {
    kicker: "Step 2",
    title: "We search for where it first appeared",
    body: "Visual embeddings compare those keyframes against everything we've indexed. This exact collision turns up a strong match, footage that's been online for years, not hours.",
  },
  {
    kicker: "Step 3",
    title: "Same footage, different story",
    body: "The earliest version we found is from a 2019 incident in a different city. The clip is real, but the context around it isn't, and that's the mismatch we surface first.",
  },
];

function StageDots({ stage }: { stage: number }) {
  return (
    <div className="flex gap-2">
      {STAGES.map((s, i) => (
        <span
          key={s.title}
          className="h-1.5 w-8 rounded-full bg-white/20 transition-colors duration-300"
          style={{ backgroundColor: stage === i ? "#7fb8ff" : undefined }}
        />
      ))}
    </div>
  );
}

function StageText({
  stage,
  reducedMotion,
  size = "lg",
}: {
  stage: number;
  reducedMotion: boolean;
  size?: "lg" | "sm";
}) {
  const current = STAGES[stage];
  const titleSize = size === "lg" ? "text-3xl sm:text-4xl" : "text-2xl";
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={stage}
        initial={reducedMotion ? false : { opacity: 0, y: 26, filter: "blur(8px)" }}
        animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
        exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: -18, filter: "blur(6px)" }}
        transition={{ duration: reducedMotion ? 0.15 : 0.65, ease: EASE_OUT_EXPO }}
      >
        <span className="text-xs font-semibold uppercase tracking-[0.2em] text-[#7fb8ff]">{current.kicker}</span>
        <h3 className={`mt-3 text-balance font-bold leading-tight tracking-[-0.02em] text-white ${titleSize}`}>
          {current.title}
        </h3>
        <p className="mt-3 text-pretty text-sm leading-relaxed text-white/70 sm:mt-4 sm:text-base">
          {current.body}
        </p>
      </motion.div>
    </AnimatePresence>
  );
}

export function PipelineExperience() {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const { progressRef, stage, reducedMotion } = useTrailScroll(wrapperRef);
  const isMobile = useIsMobile();

  const canvas = (
    <TrailCanvas
      className="absolute inset-0"
      progressRef={reducedMotion ? undefined : progressRef}
      staticStage={reducedMotion ? stage : undefined}
      reducedMotion={reducedMotion}
    />
  );

  return (
    <section ref={wrapperRef} className="relative" style={{ height: "340vh" }}>
      <div className="sticky top-0 h-[100svh] overflow-hidden">
        {isMobile ? (
          // Mobile: stacked, not side-by-side. The trail gets the full
          // width up top so it's actually visible (not squeezed into a
          // sliver) when this is screen-recorded on a phone; text sits
          // below in its own space instead of overlapping it.
          <div className="flex h-full flex-col">
            <div className="relative flex-[1.15] overflow-hidden">
              {canvas}
              <div className="pointer-events-none absolute inset-x-0 bottom-0 h-20 bg-gradient-to-b from-transparent to-[#050b24]" />
            </div>
            <div className="relative z-10 flex flex-1 flex-col justify-center gap-6 px-5 pb-8 pt-2">
              <StageText stage={stage} reducedMotion={reducedMotion} size="sm" />
              <StageDots stage={stage} />
            </div>
          </div>
        ) : (
          <>
            {/* Confined to the right side (not full-bleed) so the 3D trail never
                sits under the text column; the mask fades its own left edge in
                rather than cutting it off with a hard vertical line. */}
            <div
              className="absolute inset-y-0 right-0 w-[52%] md:w-[58%] lg:w-[64%]"
              style={{
                WebkitMaskImage: "linear-gradient(to right, transparent, black 22%)",
                maskImage: "linear-gradient(to right, transparent, black 22%)",
              }}
            >
              {canvas}
            </div>

            <div className="relative z-10 mx-auto flex h-full max-w-6xl items-center px-5 sm:px-8">
              <div className="relative min-h-[260px] max-w-md">
                <StageText stage={stage} reducedMotion={reducedMotion} />
              </div>
            </div>

            <div className="absolute bottom-10 left-1/2 z-10 -translate-x-1/2">
              <StageDots stage={stage} />
            </div>
          </>
        )}
      </div>
    </section>
  );
}
