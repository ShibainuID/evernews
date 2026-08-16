"use client";

import { useState } from "react";
import { motion } from "motion/react";
import { useReducedMotion } from "@/lib/useReducedMotion";

const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const;

const CASES = [
  {
    title: "Sari Roti Zionist Allegations",
    image: "/recent/sari-roti.jpg",
    finding: "Packaging photo predates the boycott claim by three years.",
    tag: "False Context",
  },
  {
    title: "Aqua's Water Origin Questioned",
    image: "/recent/aqua-water.jpg",
    finding: "Source label matches the current bottling plant.",
    tag: "Consistent",
  },
  {
    title: "Taxi and KRL Crash",
    image: "/recent/krl-crash.jpg",
    finding: "Footage traced to a 2019 incident in a different city.",
    tag: "False Context",
  },
];

export function ProofGallery() {
  const [tilted, setTilted] = useState<number | null>(null);
  const reducedMotion = useReducedMotion();

  return (
    <section id="proof" className="relative overflow-hidden bg-[#f5f8ff] py-24">
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 60% 50% at 85% 0%, rgba(0,50,160,0.06), transparent 70%)," +
            "radial-gradient(ellipse 50% 40% at 0% 100%, rgba(14,138,109,0.05), transparent 70%)",
        }}
      />
      <div className="relative mx-auto max-w-6xl px-5 sm:px-8">
        <motion.div
          className="max-w-2xl"
          initial={reducedMotion ? false : { opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-10% 0px" }}
          transition={{ duration: 0.7, ease: EASE_OUT_EXPO }}
        >
          <h2 className="text-balance text-[clamp(1.75rem,3.5vw,2.75rem)] font-bold leading-tight tracking-[-0.02em] text-[#0a1330]">
            Real clips, traced end to end
          </h2>
          <p className="mt-4 text-pretty text-lg leading-relaxed text-[#0a1330]/65">
            These are cases Evernews has already resolved, not mockups. Every trace shows its
            working, so you can check the evidence yourself instead of taking our word for it.
          </p>
        </motion.div>

        <div className="mt-12 grid gap-6 sm:grid-cols-3">
          {CASES.map((c, i) => (
            <motion.a
              key={c.title}
              href="/verify"
              onMouseEnter={() => setTilted(i)}
              onMouseLeave={() => setTilted(null)}
              className="group relative block overflow-hidden rounded-2xl bg-[#0a1330] transition-transform duration-500 [transition-timing-function:cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none"
              style={{
                transform: tilted === i ? "rotate(-0.6deg) translateY(-4px)" : "none",
              }}
              initial={reducedMotion ? false : { opacity: 0, y: 28 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-10% 0px" }}
              transition={{ duration: 0.6, delay: reducedMotion ? 0 : i * 0.1, ease: EASE_OUT_EXPO }}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={c.image}
                alt={`${c.title}: ${c.finding}`}
                className="h-72 w-full object-cover opacity-80 transition-opacity duration-500 group-hover:opacity-95"
              />
              <div className="absolute inset-x-0 bottom-0 h-3/4 bg-gradient-to-t from-black/90 via-black/40 to-transparent" />
              <div className="absolute inset-x-0 bottom-0 p-5">
                <span
                  className="inline-block rounded-full px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide"
                  style={{
                    backgroundColor: c.tag === "Consistent" ? "#0e8a6d" : "#b50000",
                    color: "white",
                  }}
                >
                  {c.tag}
                </span>
                <h3 className="mt-3 text-lg font-bold leading-snug text-white">{c.title}</h3>
                <p className="mt-1 text-sm leading-snug text-white/70">{c.finding}</p>
              </div>
            </motion.a>
          ))}
        </div>
      </div>
    </section>
  );
}
