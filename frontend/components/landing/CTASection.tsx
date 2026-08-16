"use client";

import Link from "next/link";
import { motion } from "motion/react";
import { ArrowRight } from "lucide-react";
import { useReducedMotion } from "@/lib/useReducedMotion";

const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const;

export function CTASection() {
  const reducedMotion = useReducedMotion();

  return (
    <section className="bg-brand py-24">
      <motion.div
        className="mx-auto max-w-3xl px-5 text-center sm:px-8"
        initial={reducedMotion ? false : { opacity: 0, y: 24 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-10% 0px" }}
        transition={{ duration: 0.7, ease: EASE_OUT_EXPO }}
      >
        <h2 className="text-balance text-[clamp(1.75rem,4vw,2.75rem)] font-bold leading-tight tracking-[-0.02em] text-white">
          Got a clip you're not sure about?
        </h2>
        <p className="mx-auto mt-4 max-w-lg text-pretty text-lg leading-relaxed text-white/75">
          Upload it, paste the caption, and see the trail for yourself. Under 15 seconds of footage,
          usually under a minute to trace.
        </p>
        <Link
          href="/verify"
          className="mt-8 inline-flex items-center gap-2 rounded-full bg-white px-7 py-3.5 text-sm font-bold text-brand transition-transform duration-300 [transition-timing-function:cubic-bezier(0.16,1,0.3,1)] hover:scale-[1.03]"
        >
          Trace your own clip
          <ArrowRight size={16} />
        </Link>
      </motion.div>
    </section>
  );
}
