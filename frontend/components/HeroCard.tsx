"use client";

import { useRef, useState, useEffect } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Plus } from "lucide-react";
import {
  submitVerification,
  VerificationError,
  type ResultClassification,
  type VerificationResult,
  type VerificationSource,
  type VerificationStage,
} from "@/lib/api";
import type { YourClipPreview } from "@/components/FindingsDetail";
import { AnalyzingScene } from "@/components/AnalyzingScene";
// One consistent, non-bouncy "step forward" feel for every stage change.
const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const;

type Stage = "idle" | "uploading" | "analyzing" | "reveal" | "done" | "error";

// The headline "reveal" moment (Figma's MISMATCH step) shown briefly before
// the full comparison detail, with one banner per classification, never red for
// a clean match.
const REVEAL_BANNER: Record<ResultClassification, { label: string; className: string }> = {
  possible_false_context: { label: "POTENTIAL MISMATCH", className: "bg-mismatch" },
  claim_conflict_found: { label: "WORTH A CLOSER LOOK", className: "bg-amber-500" },
  source_match_with_incomplete_context: { label: "PARTIAL MATCH FOUND", className: "bg-amber-500" },
  context_consistent_with_source: { label: "CONTEXT CHECKS OUT", className: "bg-consistent" },
  insufficient_evidence: { label: "WE'RE NOT SURE YET", className: "bg-black/70" },
};
const REVEAL_MS = 1400;

// Real pipeline stages (backend/state.py), in human terms, not a fixed
// animation timer. "queued" covers the moment right after upload, before
// the first status poll comes back.
const STAGE_LABELS: Record<VerificationStage | "uploading", string> = {
  uploading: "Uploading your clip...",
  queued: "Lining up the analysis...",
  preprocessing: "Finding key moments...",
  extracting_context: "Reading on-screen text & listening...",
  planning_investigation: "Planning the investigation...",
  fact_check_search: "Checking fact-check databases...",
  web_research: "Searching the web...",
  visual_source_search: "Matching against known sources...",
  synthesizing_evidence: "Weighing the evidence...",
  comparing_context: "Comparing what changed...",
  completed: "Done!",
  failed: "Something went wrong.",
};

export function HeroCard({
  onResult,
  fillHeight = false,
}: {
  onResult: (result: VerificationResult | null, preview?: YourClipPreview) => void;
  /** Desktop only: stretch to match the sidebar's height instead of sitting
   * short at the top. Pass this only while there's no findings card below;
   * once one appears, the two of them together should set the row height. */
  fillHeight?: boolean;
}) {
  const [stage, setStage] = useState<Stage>("idle");
  const [stageLabel, setStageLabel] = useState(STAGE_LABELS.uploading);
  const [currentStage, setCurrentStage] = useState<VerificationStage>("queued");
  const [preview, setPreview] = useState<YourClipPreview | null>(null);
  const [caption, setCaption] = useState("");
  const [link, setLink] = useState("");
  const [mode, setMode] = useState<"file" | "link">("file");
  const [error, setError] = useState<string | null>(null);
  const [revealResult, setRevealResult] = useState<VerificationResult | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // HeroCard remounts (fresh `key`) on "Trace another clip" while this can
  // still be mid-flight (a poll loop pending). Without this guard the stale
  // instance's poll would fire anyway once it resolves and call
  // onResult(oldResult) on the parent, silently resurrecting the findings
  // the user just reset.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function runVerification(source: VerificationSource) {
    const kind = source.file?.type.startsWith("image/") ? "image" : "video";
    const previewUrl = source.file ? URL.createObjectURL(source.file) : (source.videoUrl ?? null);
    setPreview(previewUrl ? { url: previewUrl, kind } : null);
    setError(null);
    setStage("uploading");
    setStageLabel(STAGE_LABELS.uploading);

    try {
      const result = await submitVerification(source, caption, (nextStage) => {
        if (!mountedRef.current) return;
        setStage("analyzing");
        setCurrentStage(nextStage);
        setStageLabel(STAGE_LABELS[nextStage]);
      });
      if (!mountedRef.current) return;
      setRevealResult(result);
      setStage("reveal");
      await new Promise((r) => setTimeout(r, REVEAL_MS));
      if (!mountedRef.current) return;
      onResult(result, previewUrl ? { url: previewUrl, kind } : undefined);
      setStage("done");
    } catch (err) {
      if (!mountedRef.current) return;
      const message = err instanceof VerificationError ? err.message : "That clip didn't make it through, try again.";
      setError(message);
      onResult(null);
      setStage("error");
    }
  }

  function reset() {
    setStage("idle");
    setPreview(null);
    setCaption("");
    setLink("");
    setMode("file");
    setError(null);
    setRevealResult(null);
    setCurrentStage("queued");
    onResult(null);
  }

  function handleFiles(files: FileList | null) {
    const picked = files?.[0];
    if (!picked) return;
    if (!picked.type.startsWith("video/") && !picked.type.startsWith("image/")) {
      setError("That's not a video or image file — try a short clip instead.");
      setStage("error");
      return;
    }
    runVerification({ file: picked });
  }

  function handleLinkSubmit() {
    const trimmed = link.trim();
    if (!/^https?:\/\/.+/i.test(trimmed)) {
      setError("That doesn't look like a link — it should start with http:// or https://");
      setStage("error");
      return;
    }
    runVerification({ videoUrl: trimmed });
  }

  const fill = fillHeight ? "lg:h-auto lg:flex-1" : "";
  const reduceMotion = useReducedMotion();
  const stageTransition = reduceMotion ? { duration: 0.01 } : { duration: 0.28, ease: EASE_OUT_EXPO };
  const stageVariants = reduceMotion
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 } }
    : { initial: { opacity: 0, y: 10 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -10 } };

  return (
    <section className={fillHeight ? "lg:flex lg:h-full lg:flex-col" : undefined}>
      <h1 className="text-2xl font-bold leading-snug">
        Find out whether a clip is telling{" "}
        <span className={stage === "error" || stage === "done" ? "text-mismatch italic" : "italic text-brand"}>
          the truth
        </span>{" "}
        about itself.
      </h1>
      <p className="mt-1 text-sm text-black/70">Drop in a clip of up to 15 seconds.</p>

      <div
        className={`mt-4 flex flex-col overflow-hidden rounded-card border border-black/10 bg-white p-4 shadow-sm ${fillHeight ? "lg:mt-6 lg:flex-1" : ""}`}
      >
        <AnimatePresence mode="wait" initial={false}>
          {stage === "idle" && (
            <motion.div key="idle" {...stageVariants} transition={stageTransition} className="flex flex-1 flex-col">
              <div className="flex w-fit rounded-full bg-black/5 p-0.5 text-xs font-semibold">
                <button
                  type="button"
                  onClick={() => setMode("file")}
                  className={`rounded-full px-4 py-1.5 transition ${
                    mode === "file" ? "bg-white text-black shadow-sm" : "text-black/50"
                  }`}
                >
                  Upload file
                </button>
                <button
                  type="button"
                  onClick={() => setMode("link")}
                  className={`rounded-full px-4 py-1.5 transition ${
                    mode === "link" ? "bg-white text-black shadow-sm" : "text-black/50"
                  }`}
                >
                  Paste link
                </button>
              </div>

              {mode === "file" ? (
                <>
                  <button
                    onClick={() => inputRef.current?.click()}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => {
                      e.preventDefault();
                      handleFiles(e.dataTransfer.files);
                    }}
                    className={`mt-4 flex h-40 w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-black/20 px-6 text-center text-sm text-black/50 transition hover:border-brand hover:text-brand ${fill}`}
                  >
                    <span className="flex h-[70px] w-[70px] shrink-0 items-center justify-center rounded-2xl bg-black/5">
                      <Plus size={32} strokeWidth={1.5} />
                    </span>
                    <span className="max-w-[240px]">Drop/paste the clip or image you want to trace here</span>
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
                </>
              ) : (
                <label className="mt-4 block text-xs font-medium text-black/50">
                  Paste a link to the video
                  <span className="mt-1 flex gap-2">
                    <input
                      value={link}
                      onChange={(e) => setLink(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleLinkSubmit();
                      }}
                      placeholder="https://example.com/clip.mp4"
                      className="min-w-0 flex-1 rounded-lg border border-black/10 px-3 py-2 text-sm text-black placeholder:text-black/30 focus:border-brand focus:outline-none"
                    />
                    <button
                      type="button"
                      onClick={handleLinkSubmit}
                      disabled={!link.trim()}
                      className="shrink-0 rounded-lg bg-brand px-4 py-2 text-xs font-bold text-white transition hover:opacity-90 disabled:opacity-40"
                    >
                      Trace
                    </button>
                  </span>
                </label>
              )}
            </motion.div>
          )}

          {(stage === "uploading" || stage === "analyzing") && (
            <motion.div
              key={stage}
              {...stageVariants}
              transition={stageTransition}
              className={`flex flex-col items-center justify-center gap-2 ${stage === "uploading" ? "h-40" : ""} ${fill}`}
            >
              {stage === "analyzing" && (
                <AnalyzingScene preview={preview} stage={currentStage} />
              )}
              {stage === "uploading" && (
                <div className="h-2 w-2/3 overflow-hidden rounded-full bg-black/10">
                  <motion.div
                    className="h-full w-1/2 rounded-full bg-brand"
                    animate={reduceMotion ? undefined : { x: ["-100%", "220%"] }}
                    transition={reduceMotion ? undefined : { duration: 1.1, repeat: Infinity, ease: EASE_OUT_EXPO }}
                  />
                </div>
              )}
              <AnimatePresence mode="wait">
                <motion.p
                  key={stageLabel}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.15 }}
                  className="text-xs italic text-black/50"
                >
                  {stageLabel}
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
              <button onClick={reset} className="rounded-full bg-brand px-4 py-2 text-xs font-bold text-white">
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
              Check another clip
            </motion.button>
          )}
        </AnimatePresence>
      </div>
    </section>
  );
}
