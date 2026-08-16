"use client";

import Link from "next/link";
import { motion } from "motion/react";
import { ArrowRight, PlayCircle } from "lucide-react";
import { useReducedMotion } from "@/lib/useReducedMotion";

const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const;

const lineVariants = {
  hidden: { opacity: 0, y: 22, filter: "blur(10px)" },
  visible: { opacity: 1, y: 0, filter: "blur(0px)" },
};

const riseVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0 },
};

export function Hero() {
  const reducedMotion = useReducedMotion();

  return (
    <section className="relative flex min-h-[100svh] items-center overflow-hidden">
      <div className="relative z-10 mx-auto max-w-6xl px-5 pb-16 pt-28 text-center sm:px-8">
        <h1 className="mx-auto max-w-3xl text-[clamp(2.25rem,5vw,4rem)] font-bold leading-[1.05] tracking-[-0.03em] text-white">
          <motion.span
            className="block text-balance"
            initial={reducedMotion ? false : "hidden"}
            animate="visible"
            variants={lineVariants}
            transition={{ duration: 0.8, ease: EASE_OUT_EXPO }}
          >
            The footage is real.
          </motion.span>
          <motion.span
            className="block text-balance"
            initial={reducedMotion ? false : "hidden"}
            animate="visible"
            variants={lineVariants}
            transition={{ duration: 0.8, delay: 0.12, ease: EASE_OUT_EXPO }}
          >
            The story around it might not be.
          </motion.span>
        </h1>

        <motion.p
          className="mx-auto mt-6 max-w-xl text-pretty text-lg leading-relaxed text-white/70"
          initial={reducedMotion ? false : "hidden"}
          animate="visible"
          variants={riseVariants}
          transition={{ duration: 0.7, delay: 0.4, ease: EASE_OUT_EXPO }}
        >
          Evernews traces a short clip back to where it likely first appeared, then shows you exactly
          what changed, like the location, the date, or the claim, so you can decide for yourself.
        </motion.p>

        <motion.div
          className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row"
          initial={reducedMotion ? false : "hidden"}
          animate="visible"
          variants={riseVariants}
          transition={{ duration: 0.7, delay: 0.56, ease: EASE_OUT_EXPO }}
        >
          <a
            href="#proof"
            className="inline-flex items-center gap-2 rounded-full bg-white px-6 py-3.5 text-sm font-bold text-[#050b24] transition-transform duration-300 [transition-timing-function:cubic-bezier(0.16,1,0.3,1)] hover:scale-[1.03]"
          >
            <PlayCircle size={18} />
            See a live trace
          </a>
          <Link
            href="/verify"
            className="inline-flex items-center gap-2 rounded-full border border-white/25 px-6 py-3.5 text-sm font-semibold text-white transition-colors hover:bg-white/10"
          >
            Trace your own clip
            <ArrowRight size={16} />
          </Link>
        </motion.div>
      </div>

      <motion.div
        className="pointer-events-none absolute inset-x-0 bottom-6 z-10 flex flex-col items-center gap-2 text-white/40"
        initial={reducedMotion ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.6, delay: 0.9 }}
      >
        <span className="text-xs font-semibold uppercase tracking-[0.2em]">Scroll to see how</span>
        <span className="h-8 w-px animate-pulse bg-white/30 motion-reduce:animate-none" />
      </motion.div>
    </section>
  );
}
