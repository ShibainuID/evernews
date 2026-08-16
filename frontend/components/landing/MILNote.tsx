"use client";

import { motion } from "motion/react";
import { useReducedMotion } from "@/lib/useReducedMotion";

const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const;

export function MILNote() {
  const reducedMotion = useReducedMotion();

  return (
    <section className="bg-white py-20">
      <motion.div
        className="mx-auto max-w-3xl px-5 text-center sm:px-8"
        initial={reducedMotion ? false : { opacity: 0, y: 24 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-10% 0px" }}
        transition={{ duration: 0.7, ease: EASE_OUT_EXPO }}
      >
        <h2 className="text-balance text-2xl font-bold leading-snug tracking-[-0.02em] text-[#0a1330] sm:text-3xl">
          We show you the evidence. You make the call.
        </h2>
        <p className="mx-auto mt-4 max-w-xl text-pretty leading-relaxed text-[#0a1330]/65">
          Evernews never tells you a clip is "fake." Authentic footage gets reused with a new time,
          place, or story more often than it gets faked outright, and that's{" "}
          <span className="font-semibold text-[#0032a0]">false context</span>, and understanding how
          it happens is more useful than a single verdict.
        </p>
      </motion.div>
    </section>
  );
}
