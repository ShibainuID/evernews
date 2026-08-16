"use client";

import { useState, type ReactElement } from "react";
import { Check, TriangleAlert, Minus, Share2 } from "lucide-react";
import type { ComparisonStatus, DimensionComparison, ResultClassification, VerificationResult } from "@/lib/api";

const BANNER: Record<ResultClassification, { label: string; className: string }> = {
  possible_false_context: { label: "POTENTIAL MISMATCH", className: "bg-mismatch text-white" },
  claim_conflict_found: { label: "WORTH A CLOSER LOOK", className: "bg-amber-500 text-white" },
  source_match_with_incomplete_context: { label: "PARTIAL MATCH FOUND", className: "bg-amber-500 text-white" },
  context_consistent_with_source: { label: "CONTEXT CHECKS OUT", className: "bg-consistent text-white" },
  insufficient_evidence: { label: "WE'RE NOT SURE YET", className: "bg-black/70 text-white" },
};

const MATCH_LABEL: Record<string, string> = { high: "High match", medium: "Medium match", low: "Low match", unknown: "No match found" };

const STATUS_ICON: Record<ComparisonStatus, ReactElement> = {
  consistent: <Check size={16} className="text-consistent" />,
  mismatch: <TriangleAlert size={16} className="text-amber-500" />,
  unknown: <Minus size={16} className="text-black/30" />,
};

function DimensionRow({ label, dim }: { label: string; dim: DimensionComparison }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-black/5 py-3 last:border-0">
      <div className="flex items-center gap-2">
        {STATUS_ICON[dim.status]}
        <span className="text-sm font-semibold">{label}</span>
      </div>
      <p className="max-w-[60%] text-right text-xs text-black/60">{dim.explanation}</p>
    </div>
  );
}

const MIL_COPY: Record<string, { title: string; body: string }> = {
  "False Context": {
    title: "This is called False Context.",
    body: "False Context happens when authentic media is reused with information about a different time, location, or event. The footage itself isn't fabricated — the story wrapped around it is.",
  },
};

export function FindingsDetail({ result, onReset }: { result: VerificationResult; onReset: () => void }) {
  const [copied, setCopied] = useState(false);
  const banner = BANNER[result.classification];
  const source = result.source_context;
  const lesson = result.manipulation_types[0] ? MIL_COPY[result.manipulation_types[0]] : null;

  async function share() {
    const text = `${banner.label}: ${result.headline}\n\nCurrent claim: ${result.comparison.location.current ?? "unknown"}, ${result.comparison.date.current ?? "unknown"}\nEarliest match: ${source?.location ?? "unknown"}, ${source?.date ?? "unknown"}\nVisual match: ${MATCH_LABEL[result.visual_match]}\n\nChecked with Evernews.`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ponytail: clipboard can be denied by the browser; the text is still visible on-screen
    }
  }

  return (
    <section className="mx-4 mt-4 space-y-4">
      <div className={`rounded-card px-5 py-4 ${banner.className}`}>
        <p className="text-sm font-medium opacity-90">We have found:</p>
        <p className="text-xl font-extrabold tracking-tight">{banner.label}</p>
      </div>

      <div className="rounded-card bg-white p-5">
        <h3 className="text-lg font-bold">{result.headline}</h3>
        <p className="mt-1 text-sm leading-relaxed text-black/70">{result.summary}</p>

        {source && (
          <div className="mt-4 grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-black/5 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-black/40">Your Video Clip</p>
              <p className="mt-1 text-sm font-semibold">{result.comparison.location.current ?? "Unspecified location"}</p>
              <p className="text-xs text-black/50">{result.comparison.date.current ?? "Unspecified date"}</p>
            </div>
            <div className="rounded-xl bg-black/5 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-black/40">Earliest Match Found</p>
              <p className="mt-1 text-sm font-semibold">{source.location ?? "Unknown"}</p>
              <p className="text-xs text-black/50">{source.date ?? "Unknown"} · {source.publisher ?? "Unknown publisher"}</p>
            </div>
          </div>
        )}

        <p className="mt-3 text-xs font-medium text-black/50">
          Visual match: <span className="font-semibold text-black/70">{MATCH_LABEL[result.visual_match]}</span>
        </p>
      </div>

      <div className="rounded-card bg-white p-5">
        <h3 className="text-sm font-bold">What matched, what didn't</h3>
        <div className="mt-2">
          <DimensionRow label="Event" dim={result.comparison.event} />
          <DimensionRow label="Location" dim={result.comparison.location} />
          <DimensionRow label="Date" dim={result.comparison.date} />
        </div>
      </div>

      {lesson && (
        <div className="rounded-card bg-white p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-black/40">What happened?</p>
          <h3 className="mt-1 text-base font-bold">{lesson.title}</h3>
          <p className="mt-1 text-sm leading-relaxed text-black/70">{lesson.body}</p>
        </div>
      )}

      {result.unresolved.length > 0 && (
        <div className="rounded-card bg-white p-5 text-xs text-black/50">
          <p className="font-semibold text-black/60">Still unclear:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {result.unresolved.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex gap-3">
        <button
          onClick={share}
          className="flex flex-1 items-center justify-center gap-2 rounded-full bg-brand py-3 text-sm font-bold text-white"
        >
          <Share2 size={16} /> {copied ? "Copied!" : "Share this finding"}
        </button>
        <button onClick={onReset} className="rounded-full border border-black/10 px-4 py-3 text-sm font-semibold">
          Check another
        </button>
      </div>
    </section>
  );
}
