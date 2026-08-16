"use client";

import { useEffect, useRef, useState } from "react";
import { Plus } from "lucide-react";
import { submitVerification, VerificationError, type VerificationResult } from "@/lib/api";

type Stage = "idle" | "uploading" | "analyzing" | "done" | "error";

const UPLOADING_FRAMES = ["Uploading.", "Uploading..", "Uploading...", "Uploading Success!"];
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
    const id = setInterval(() => setIndex((i) => (i + 1) % frames.length), FRAME_MS);
    return () => clearInterval(id);
  }, [active, frames]);
  return frames[index];
}

export function HeroCard({ onResult }: { onResult: (result: VerificationResult | null) => void }) {
  const [stage, setStage] = useState<Stage>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [caption, setCaption] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const uploadingText = useCyclingFrame(UPLOADING_FRAMES, stage === "uploading");
  const analyzingText = useCyclingFrame(ANALYZING_FRAMES, stage === "analyzing");

  async function runVerification(selected: File) {
    setFile(selected);
    setPreviewUrl(URL.createObjectURL(selected));
    setError(null);
    setStage("uploading");

    const requestPromise = submitVerification(selected, caption);
    await new Promise((r) => setTimeout(r, UPLOADING_FRAMES.length * FRAME_MS));
    setStage("analyzing");

    try {
      const result = await requestPromise;
      await new Promise((r) => setTimeout(r, 900));
      onResult(result);
      setStage("done");
    } catch (err) {
      const message = err instanceof VerificationError ? err.message : "That clip didn't make it through — try again.";
      setError(message);
      onResult(null);
      setStage("error");
    }
  }

  function reset() {
    setStage("idle");
    setFile(null);
    setPreviewUrl(null);
    setCaption("");
    setError(null);
    onResult(null);
  }

  function handleFiles(files: FileList | null) {
    const picked = files?.[0];
    if (!picked) return;
    if (!picked.type.startsWith("video/")) {
      setError("That's not a video file — try a clip instead of a photo or document.");
      setStage("error");
      return;
    }
    runVerification(picked);
  }

  return (
    <section className="mx-4 mt-5">
      <h1 className="text-2xl font-bold leading-snug">
        Find out whether a clip is telling{" "}
        <span className={stage === "error" || stage === "done" ? "text-mismatch italic" : "italic text-brand"}>
          the truth
        </span>{" "}
        about itself.
      </h1>
      <p className="mt-1 text-sm text-black/70">Drop in a clip of up to 15 seconds.</p>

      <div className="mt-4 rounded-card border border-black/10 bg-white p-4 shadow-sm">
        {stage === "idle" && (
          <>
            <button
              onClick={() => inputRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                handleFiles(e.dataTransfer.files);
              }}
              className="flex h-40 w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-black/20 text-sm text-black/50 transition hover:border-brand hover:text-brand"
            >
              <span className="flex h-[70px] w-[70px] items-center justify-center rounded-2xl bg-black/5">
                <Plus size={32} strokeWidth={1.5} />
              </span>
              Drop/paste the clip you want to trace here
            </button>
            <input
              ref={inputRef}
              type="file"
              accept="video/*"
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
        )}

        {stage === "uploading" && (
          <div className="flex h-40 flex-col items-center justify-center gap-2">
            <div className="h-2 w-2/3 overflow-hidden rounded-full bg-black/10">
              <div className="h-full w-1/2 animate-pulse rounded-full bg-brand" />
            </div>
            <p className="text-xs italic text-black/50">{uploadingText}</p>
          </div>
        )}

        {stage === "analyzing" && (
          <div className="flex flex-col items-center gap-2">
            {previewUrl && (
              <video src={previewUrl} className="h-40 w-auto rounded-xl object-cover" muted playsInline autoPlay loop />
            )}
            <p className="text-xs italic text-black/50">{analyzingText}</p>
          </div>
        )}

        {stage === "error" && (
          <div className="flex h-40 flex-col items-center justify-center gap-3 text-center">
            <p className="text-sm text-black/70">{error}</p>
            <button onClick={reset} className="rounded-full bg-brand px-4 py-2 text-xs font-bold text-white">
              Try again
            </button>
          </div>
        )}

        {stage === "done" && (
          <button onClick={reset} className="w-full text-left text-xs font-semibold text-brand underline">
            Check another video
          </button>
        )}
      </div>
    </section>
  );
}
