"use client";

import { useState, type ReactElement } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Check, TriangleAlert, Minus, MoreHorizontal } from "lucide-react";
import { FaWhatsapp, FaInstagram, FaXTwitter, FaTiktok, FaThreads, FaYoutube, FaFacebook } from "react-icons/fa6";
import type { ComparisonStatus, DimensionComparison, ResultClassification, VerificationResult } from "@/lib/api";

const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const;

export interface YourClipPreview {
  url: string;
  kind: "video" | "image";
}

const PILL: Record<ResultClassification, { label: string; className: string }> = {
  possible_false_context: { label: "Potential Information Mismatch", className: "border-mismatch text-mismatch" },
  claim_conflict_found: { label: "Worth a Closer Look", className: "border-amber-500 text-amber-600" },
  source_match_with_incomplete_context: {
    label: "Partial Match Found",
    className: "border-amber-500 text-amber-600",
  },
  context_consistent_with_source: { label: "Context Checks Out", className: "border-consistent text-consistent" },
  insufficient_evidence: { label: "We're Not Sure Yet", className: "border-black/20 text-black/60" },
};

const MATCH_LABEL: Record<string, string> = {
  high: "High match",
  medium: "Medium match",
  low: "Low match",
  unknown: "No match found",
};

const STATUS_ICON: Record<ComparisonStatus, ReactElement> = {
  consistent: <Check size={16} className="text-consistent" />,
  mismatch: <TriangleAlert size={16} className="text-amber-500" />,
  unknown: <Minus size={16} className="text-black/30" />,
};

const SHARE_TARGETS = [
  { Icon: FaWhatsapp, color: "#25D366", label: "WhatsApp" },
  { Icon: FaInstagram, color: "#E1306C", label: "Instagram" },
  { Icon: FaXTwitter, color: "#000000", label: "X" },
  { Icon: FaTiktok, color: "#000000", label: "TikTok" },
  { Icon: FaThreads, color: "#000000", label: "Threads" },
  { Icon: FaYoutube, color: "#FF0000", label: "YouTube" },
  { Icon: FaFacebook, color: "#1877F2", label: "Facebook" },
];

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

function ClipFrame({ children }: { children: React.ReactNode }) {
  return <div className="aspect-[140/249] w-full overflow-hidden rounded-xl bg-black/5">{children}</div>;
}

export function FindingsDetail({
  result,
  yourClip,
  onReset,
}: {
  result: VerificationResult;
  yourClip: YourClipPreview | null;
  onReset: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const pill = PILL[result.classification];
  const source = result.source_context;
  const lesson = result.manipulation_types[0] ? MIL_COPY[result.manipulation_types[0]] : null;
  const reduceMotion = useReducedMotion();

  // Cards step in one after another rather than popping in as a block —
  // the reveal should read as a sequence of findings, not a single flash.
  const container = {
    hidden: {},
    visible: { transition: { staggerChildren: reduceMotion ? 0 : 0.09, delayChildren: reduceMotion ? 0 : 0.05 } },
  };
  const item = reduceMotion
    ? { hidden: { opacity: 0 }, visible: { opacity: 1 } }
    : {
        hidden: { opacity: 0, y: 16 },
        visible: { opacity: 1, y: 0, transition: { duration: 0.32, ease: EASE_OUT_EXPO } },
      };

  async function share() {
    const text = `${pill.label}\n\n${result.summary}\n\nVisual match: ${MATCH_LABEL[result.visual_match]}\n\nChecked with Evernews.`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ponytail: clipboard can be denied by the browser; "Trace another clip" still works either way
    }
  }

  return (
    <motion.section className="space-y-4" variants={container} initial="hidden" animate="visible">
      <motion.div variants={item} className="rounded-card bg-white p-5">
        <div
          className={`mx-auto w-fit rounded-full border px-4 py-1.5 text-center text-base font-bold ${pill.className}`}
        >
          {pill.label}
        </div>
        {result.confidence_score != null && (
          <p className="mt-3 text-center text-sm font-bold">
            Evernews Confidence Score: <span className="text-brand">{result.confidence_score}/100</span>
          </p>
        )}
        <p className="mt-3 text-sm leading-relaxed text-black/70">{result.summary}</p>

        <div className="mt-4 grid grid-cols-2 gap-3">
          <div>
            <p className="text-center text-xs font-semibold">Original Video</p>
            <ClipFrame>
              <div className="flex h-full items-center justify-center p-2 text-center text-[10px] text-black/40">
                {source?.publisher ?? "No source found"}
              </div>
            </ClipFrame>
            <p className="mt-1 text-center text-[10px] text-black/50">{source?.date ?? "Unknown date"}</p>
          </div>
          <div>
            <p className="text-center text-xs font-semibold">Your Video Clip</p>
            <ClipFrame>
              {yourClip && yourClip.kind === "image" ? (
                <img src={yourClip.url} alt="" className="h-full w-full object-contain" />
              ) : (
                yourClip && <video src={yourClip.url} className="h-full w-full object-cover" muted playsInline loop autoPlay />
              )}
            </ClipFrame>
            <p className="mt-1 text-center text-[10px] text-black/50">Uploaded just now</p>
          </div>
        </div>

        <p className="mt-4 text-xs font-medium text-black/50">
          Visual match: <span className="font-semibold text-black/70">{MATCH_LABEL[result.visual_match]}</span>
        </p>

        <div className="mt-4">
          <p className="text-xs font-semibold text-brand">Share this finding with others:</p>
          <div className="mt-2 flex items-center gap-1 rounded-full border border-brand/30 px-3 py-2">
            {SHARE_TARGETS.map(({ Icon, color, label }) => (
              <button
                key={label}
                aria-label={`Share to ${label}`}
                onClick={share}
                className="flex h-8 w-8 items-center justify-center rounded-full hover:bg-black/5"
                style={{ color }}
              >
                <Icon size={18} />
              </button>
            ))}
            <button
              aria-label="Copy finding as text"
              onClick={share}
              className="ml-auto flex h-8 w-8 items-center justify-center rounded-full text-black/40 hover:bg-black/5"
            >
              <MoreHorizontal size={18} />
            </button>
          </div>
          {copied && <p className="mt-1 text-right text-[10px] font-medium text-brand">Copied to clipboard!</p>}
        </div>

        <button onClick={onReset} className="mt-4 w-full rounded-full bg-brand py-3 text-sm font-bold text-white">
          Trace another clip
        </button>
      </motion.div>

      <motion.div variants={item} className="rounded-card bg-white p-5">
        <h3 className="text-sm font-bold">What matched, what didn't</h3>
        <div className="mt-2">
          <DimensionRow label="Event" dim={result.comparison.event} />
          <DimensionRow label="Location" dim={result.comparison.location} />
          <DimensionRow label="Date" dim={result.comparison.date} />
        </div>
      </motion.div>

      {lesson && (
        <motion.div variants={item} className="rounded-card bg-white p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-black/40">What happened?</p>
          <h3 className="mt-1 text-base font-bold">{lesson.title}</h3>
          <p className="mt-1 text-sm leading-relaxed text-black/70">{lesson.body}</p>
        </motion.div>
      )}

      {result.unresolved.length > 0 && (
        <motion.div variants={item} className="rounded-card bg-white p-5 text-xs text-black/50">
          <p className="font-semibold text-black/60">Still unclear:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {result.unresolved.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </motion.div>
      )}
    </motion.section>
  );
}
