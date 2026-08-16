"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Plus } from "lucide-react";
import {
  submitVerification,
  VerificationError,
  type ResultClassification,
  type VerificationResult,
} from "@/lib/api";
import type { YourClipPreview } from "@/components/FindingsDetail";

// One consistent, non-bouncy "step forward" feel for every stage change.
const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const;

type Stage = "idle" | "uploading" | "analyzing" | "reveal" | "done" | "error";

// The headline "reveal" moment (Figma's MISMATCH step) shown briefly before
// the full comparison detail — one banner per classification, never red for
// a clean match.
const REVEAL_BANNER: Record<
  ResultClassification,
  { label: string; className: string }
> = {
  possible_false_context: {
    label: "POTENTIAL MISMATCH",
    className: "bg-mismatch",
  },
  claim_conflict_found: {
    label: "WORTH A CLOSER LOOK",
    className: "bg-amber-500",
  },
  source_match_with_incomplete_context: {
    label: "PARTIAL MATCH FOUND",
    className: "bg-amber-500",
  },
  context_consistent_with_source: {
    label: "CONTEXT CHECKS OUT",
    className: "bg-consistent",
  },
  insufficient_evidence: {
    label: "WE'RE NOT SURE YET",
    className: "bg-black/70",
  },
};
const REVEAL_MS = 1400;

const UPLOADING_FRAMES = [
  "Uploading.",
  "Uploading..",
  "Uploading...",
  "Uploading Success!",
];
const ANALYZING_FRAMES = [
  "Analyzing.",
  "Analyzing..",
  "Analyzing...",
  "Figuring out the source...",
  "Figuring out the source..",
  "Figuring out the source.",
  "Recontextualizing.",
  "Recontextualizing..",
  "Recontextualizing...",
];
const FRAME_MS = 700;

function useCyclingFrame(frames: string[], active: boolean) {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (!active) return;
    setIndex(0);
    const id = setInterval(
      () => setIndex((i) => (i + 1) % frames.length),
      FRAME_MS,
    );
    return () => clearInterval(id);
  }, [active, frames]);
  return frames[index];
}

export function HeroCard({
  onResult,
  fillHeight = false,
}: {
  onResult: (
    result: VerificationResult | null,
    preview?: YourClipPreview,
  ) => void;
  /** Desktop only: stretch to match the sidebar's height instead of sitting
   * short at the top. Pass this only while there's no findings card below —
   * once one appears, the two of them together should set the row height. */
  fillHeight?: boolean;
}) {
  const [stage, setStage] = useState<Stage>("idle");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isImage, setIsImage] = useState(false);
  const [caption, setCaption] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [revealResult, setRevealResult] = useState<VerificationResult | null>(
    null,
  );
  const inputRef = useRef<HTMLInputElement>(null);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const uploadingText = useCyclingFrame(
    UPLOADING_FRAMES,
    stage === "uploading",
  );
  const analyzingText = useCyclingFrame(
    ANALYZING_FRAMES,
    stage === "analyzing",
  );

  async function runVerification(selected: File) {
    const selectedIsImage = selected.type.startsWith("image/");
    const objectUrl = URL.createObjectURL(selected);
    setIsImage(selectedIsImage);
    setPreviewUrl(objectUrl);
    setError(null);
    setStage("uploading");

    const requestPromise = submitVerification(selected, caption);
    await new Promise((r) => setTimeout(r, UPLOADING_FRAMES.length * FRAME_MS));
    if (!mountedRef.current) return;
    setStage("analyzing");

    try {
      const result = await requestPromise;
      if (!mountedRef.current) return;
      setRevealResult(result);
      setStage("reveal");
      await new Promise((r) => setTimeout(r, REVEAL_MS));
      if (!mountedRef.current) return;
      onResult(result, { url: objectUrl, isImage: selectedIsImage });
      setStage("done");
    } catch (err) {
      if (!mountedRef.current) return;
      const message =
        err instanceof VerificationError
          ? err.message
          : "That clip didn't make it through — try again.";
      setError(message);
      onResult(null);
      setStage("error");
    }
  }

  function reset() {
    setStage("idle");
    setPreviewUrl(null);
    setIsImage(false);
    setCaption("");
    setError(null);
    setRevealResult(null);
    onResult(null);
  }

  function handleFiles(files: FileList | null) {
    const picked = files?.[0];
    if (!picked) return;
    if (
      !picked.type.startsWith("video/") &&
      !picked.type.startsWith("image/")
    ) {
      setError(
        "That's not a clip or a photo we can read — try a video or an image file instead.",
      );
      setStage("error");
      return;
    }
    runVerification(picked);
  }

  const fill = fillHeight ? "lg:h-auto lg:flex-1" : "";
  const reduceMotion = useReducedMotion();
  const stageTransition = reduceMotion
    ? { duration: 0.01 }
    : { duration: 0.28, ease: EASE_OUT_EXPO };
  const stageVariants = reduceMotion
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 } }
    : {
        initial: { opacity: 0, y: 10 },
        animate: { opacity: 1, y: 0 },
        exit: { opacity: 0, y: -10 },
      };

  return (
    <section
      className={fillHeight ? "lg:flex lg:h-full lg:flex-col" : undefined}
    >
      <h1 className="text-2xl font-bold leading-snug">
        Find out whether a clip is telling{" "}
        <span
          className={
            stage === "error" || stage === "done"
              ? "text-mismatch italic"
              : "italic text-brand"
          }
        >
          the truth
        </span>{" "}
        about itself.
      </h1>
      <p className="mt-1 text-sm text-black/70">
        Drop in a clip of up to 15 seconds, or a photo.
      </p>

      <div
        className={`mt-4 flex flex-col overflow-hidden rounded-card border border-black/10 bg-white p-4 shadow-sm ${fillHeight ? "lg:mt-6 lg:flex-1" : ""}`}
      >
        <AnimatePresence mode="wait" initial={false}>
          {stage === "idle" && (
            <motion.div
              key="idle"
              {...stageVariants}
              transition={stageTransition}
              className="flex flex-1 flex-col"
            >
              <button
                onClick={() => inputRef.current?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  handleFiles(e.dataTransfer.files);
                }}
                className={`flex h-40 w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-black/20 px-6 text-center text-sm text-black/50 transition hover:border-brand hover:text-brand ${fill}`}
              >
                <span className="flex h-[70px] w-[70px] shrink-0 items-center justify-center rounded-2xl bg-black/5">
                  <Plus size={32} strokeWidth={1.5} />
                </span>
                <span className="max-w-[240px]">
                  Drop/paste the clip or photo you want to trace here
                </span>
              </button>
              <input
                ref={inputRef}
                type="file"
                accept="video/*,image/*"
                className="hidden"
                onChange={(e) => handleFiles(e.target.files)}
              />
              <label className="mt-3 block text-xs font-medium text-black/50">
                Got a caption or claim that came with it? (optional)
                <input
                  value={caption}
                  onChange={(e) => setCaption(e.target.value)}
                  placeholder="e.g. Jakarta is flooding today"
                  className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 text-sm text-black placeholder:text-black/30 focus:border-brand focus:outline-none"
                />
              </label>
            </motion.div>
          )}

          {stage === "uploading" && (
            <motion.div
              key="uploading"
              {...stageVariants}
              transition={stageTransition}
              className={`flex h-40 flex-col items-center justify-center gap-2 ${fill}`}
            >
              <div className="h-2 w-2/3 overflow-hidden rounded-full bg-black/10">
                <motion.div
                  className="h-full w-1/2 rounded-full bg-brand"
                  animate={reduceMotion ? undefined : { x: ["-100%", "220%"] }}
                  transition={
                    reduceMotion
                      ? undefined
                      : { duration: 1.1, repeat: Infinity, ease: EASE_OUT_EXPO }
                  }
                />
              </div>
              <AnimatePresence mode="wait">
                <motion.p
                  key={uploadingText}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.15 }}
                  className="text-xs italic text-black/50"
                >
                  {uploadingText}
                </motion.p>
              </AnimatePresence>
            </motion.div>
          )}

          {stage === "analyzing" && (
            <motion.div
              key="analyzing"
              {...stageVariants}
              transition={stageTransition}
              className={`flex flex-col items-center justify-center gap-2 ${fill}`}
            >
              {previewUrl && isImage && (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={previewUrl}
                  alt=""
                  className="h-40 w-auto rounded-xl object-cover"
                />
              )}
              {previewUrl && !isImage && (
                <video
                  src={previewUrl}
                  className="h-40 w-auto rounded-xl object-cover"
                  muted
                  playsInline
                  autoPlay
                  loop
                />
              )}
              <AnimatePresence mode="wait">
                <motion.p
                  key={analyzingText}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.15 }}
                  className="text-xs italic text-black/50"
                >
                  {analyzingText}
                </motion.p>
              </AnimatePresence>
            </motion.div>
          )}

          {stage === "reveal" && revealResult && (
            <motion.div
              key="reveal"
              initial={{ opacity: 0, scale: reduceMotion ? 1 : 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0 }}
              transition={stageTransition}
              className={`flex h-40 flex-col items-center justify-center gap-1 rounded-xl text-center text-white ${fill} ${REVEAL_BANNER[revealResult.classification].className}`}
            >
              <p className="text-sm font-medium opacity-90">We have found:</p>
              <p className="px-4 text-xl font-extrabold tracking-tight">
                {REVEAL_BANNER[revealResult.classification].label}
              </p>
            </motion.div>
          )}

          {stage === "error" && (
            <motion.div
              key="error"
              {...stageVariants}
              transition={stageTransition}
              className={`flex h-40 flex-col items-center justify-center gap-3 text-center ${fill}`}
            >
              <p className="text-sm text-black/70">{error}</p>
              <button
                onClick={reset}
                className="rounded-full bg-brand px-4 py-2 text-xs font-bold text-white"
              >
                Try again
              </button>
            </motion.div>
          )}

          {stage === "done" && (
            <motion.button
              key="done"
              {...stageVariants}
              transition={stageTransition}
              onClick={reset}
              className="w-full text-left text-xs font-semibold text-brand underline"
            >
              Check another clip or photo
            </motion.button>
          )}
        </AnimatePresence>
      </div>
    </section>
  );
}
