"use client";

import { motion, useReducedMotion } from "motion/react";
import type { VerificationStage } from "@/lib/api";
import type { YourClipPreview } from "@/components/FindingsDetail";

const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const;

// Real pipeline stages condensed into five readable beats. The active node
// tracks the live backend stage — never a fixed animation timer.
const NODES: { key: string; label: string; stages: VerificationStage[] }[] = [
  { key: "extract", label: "Extract", stages: ["preprocessing", "extracting_context"] },
  {
    key: "search",
    label: "Search",
    stages: ["planning_investigation", "fact_check_search", "web_research"],
  },
  { key: "match", label: "Match", stages: ["visual_source_search"] },
  { key: "weigh", label: "Weigh", stages: ["synthesizing_evidence"] },
  { key: "compare", label: "Compare", stages: ["comparing_context", "completed"] },
];

function nodeIndex(stage: VerificationStage): number {
  const idx = NODES.findIndex((n) => n.stages.includes(stage));
  return idx === -1 ? -1 : idx;
}

/** The "analyzing" scene: forensic scan over the clip + live pipeline beam.
 * Pure motion/CSS, no new dependencies; falls back to a static state when the
 * user prefers reduced motion. */
export function AnalyzingScene({
  preview,
  stage,
}: {
  preview: YourClipPreview | null;
  stage: VerificationStage;
}) {
  const reduceMotion = useReducedMotion();
  const active = nodeIndex(stage);
  const scanDur = reduceMotion ? 0 : 2.4;
  const pingDur = reduceMotion ? 0 : 2.2;

  return (
    <div className="w-full max-w-sm">
      <div className="relative aspect-video w-full overflow-hidden rounded-xl bg-black">
        {preview ? (
          preview.kind === "image" ? (
            <img src={preview.url} alt="" className="h-full w-full object-contain" />
          ) : (
            <video
              src={preview.url}
              className="h-full w-full object-contain"
              muted
              playsInline
              autoPlay
              loop
            />
          )
        ) : (
          <div className="h-full w-full bg-gradient-to-br from-black/80 via-black/60 to-black/80" />
        )}

        {/* faint scan grid */}
        {!reduceMotion && (
          <div
            className="pointer-events-none absolute inset-0 opacity-20"
            style={{
              backgroundImage:
                "linear-gradient(rgba(255,255,255,.25) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.25) 1px, transparent 1px)",
              backgroundSize: "28px 28px",
            }}
          />
        )}

        {/* viewfinder corner brackets */}
        {[
          "left-2 top-2 border-l-2 border-t-2",
          "right-2 top-2 border-r-2 border-t-2",
          "bottom-2 left-2 border-b-2 border-l-2",
          "bottom-2 right-2 border-b-2 border-r-2",
        ].map((pos) => (
          <div
            key={pos}
            className={`absolute h-4 w-4 border-emerald-300/80 ${pos}`}
          />
        ))}

        {/* expanding radar pings from the center */}
        {!reduceMotion && (
          <>
            <motion.div
              className="pointer-events-none absolute left-1/2 top-1/2 h-16 w-16 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-emerald-300/70"
              initial={{ scale: 0.2, opacity: 0.7 }}
              animate={{ scale: 3.2, opacity: 0 }}
              transition={{ duration: pingDur, repeat: Infinity, ease: "easeOut" }}
            />
            <motion.div
              className="pointer-events-none absolute left-1/2 top-1/2 h-16 w-16 -translate-x-1/2 -translate-y-1/2 rounded-full border border-emerald-300/50"
              initial={{ scale: 0.2, opacity: 0.5 }}
              animate={{ scale: 3.2, opacity: 0 }}
              transition={{ duration: pingDur, repeat: Infinity, ease: "easeOut", delay: pingDur / 2 }}
            />
          </>
        )}

        {/* sweeping scanline */}
        {!reduceMotion && (
          <motion.div
            className="pointer-events-none absolute inset-x-0 h-16"
            style={{
              background:
                "linear-gradient(to bottom, transparent, rgba(52,211,153,.35) 55%, rgba(52,211,153,.7))",
            }}
            animate={{ top: ["-18%", "100%"] }}
            transition={{ duration: scanDur, repeat: Infinity, ease: "linear" }}
          />
        )}

        {/* live badge */}
        <div className="absolute right-2 top-2 flex items-center gap-1.5 rounded-full bg-black/55 px-2 py-0.5 text-[10px] font-semibold tracking-widest text-emerald-300 backdrop-blur-sm">
          <span className="relative flex h-1.5 w-1.5">
            {!reduceMotion && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            )}
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
          </span>
          ANALYZING
        </div>
      </div>

      {/* pipeline beam */}
      <div className="mt-4">
        <div className="relative flex items-start justify-between">
          <div className="absolute left-0 right-0 top-3.5 h-px bg-black/10" />
          {!reduceMotion && active > 0 && (
            <motion.div
              className="absolute left-0 top-3.5 h-px bg-gradient-to-r from-brand via-emerald-400 to-brand"
              initial={false}
              animate={{ width: `${(active / (NODES.length - 1)) * 100}%` }}
              transition={{ duration: 0.5, ease: EASE_OUT_EXPO }}
              style={{ filter: "drop-shadow(0 0 4px rgba(52,211,153,.8))" }}
            />
          )}
          {NODES.map((node, i) => {
            const done = active > i;
            const isActive = active === i;
            return (
              <div key={node.key} className="relative z-10 flex w-14 flex-col items-center gap-1.5">
                <motion.div
                  className="flex h-7 w-7 items-center justify-center rounded-full border text-[11px] font-bold"
                  animate={{
                    backgroundColor: done || isActive ? "rgba(16,185,129,1)" : "rgba(255,255,255,1)",
                    borderColor: done || isActive ? "rgba(16,185,129,1)" : "rgba(0,0,0,.15)",
                    color: done || isActive ? "rgba(255,255,255,1)" : "rgba(0,0,0,.45)",
                    scale: isActive && !reduceMotion ? 1.12 : 1,
                  }}
                  transition={{ duration: 0.3, ease: EASE_OUT_EXPO }}
                >
                  {isActive && !reduceMotion ? (
                    <motion.span
                      className="h-2 w-2 rounded-full bg-white"
                      animate={{ scale: [1, 0.4, 1] }}
                      transition={{ duration: 0.9, repeat: Infinity, ease: "easeInOut" }}
                    />
                  ) : done ? (
                    "✓"
                  ) : (
                    i + 1
                  )}
                </motion.div>
                <span
                  className={`text-[10px] font-medium tracking-wide ${
                    done || isActive ? "text-emerald-600" : "text-black/35"
                  }`}
                >
                  {node.label}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
