"use client";

import { Upload, ScanText, Video, FlagTriangleRight } from "lucide-react";

const STEPS = [
  { icon: Upload, text: "You insert your clip for us to analyze" },
  { icon: ScanText, text: "We pull keyframes, on-screen text, & speech" },
  { icon: Video, text: "We match against known sources using visual embeddings" },
  {
    icon: FlagTriangleRight,
    text: "We check for recontextualization and flag if there's a mismatch then show you all versions.",
  },
];

export function HowItWorks() {
  return (
    <section className="mx-4 mt-4 rounded-card bg-white p-5">
      <h2 className="text-xl font-bold">How Evernews Works</h2>
      <ol className="mt-4 space-y-4">
        {STEPS.map(({ icon: Icon, text }, i) => (
          <li key={i} className="flex items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand text-white">
              <Icon size={18} />
            </span>
            <p className="pt-2 text-sm font-medium leading-snug">{text}</p>
          </li>
        ))}
      </ol>
      <a
        href="mailto:hello@evernews.app"
        className="mt-5 block rounded-full bg-brand py-3 text-center text-sm font-bold text-white"
      >
        Contact us for more info! <span className="underline">Click here</span>
      </a>
    </section>
  );
}
