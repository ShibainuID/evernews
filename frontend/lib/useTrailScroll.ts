"use client";

import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "./useReducedMotion";

export const STAGE_COUNT = 3;

/**
 * Tracks how far the viewport has moved through a tall wrapper element,
 * expressed as 0..1. Written to a ref every frame (read inside R3F's
 * useFrame) so the 3D camera stays smooth without triggering React
 * re-renders on every scroll pixel. `stage` is plain state because it only
 * flips STAGE_COUNT - 1 times per scroll pass.
 */
export function useTrailScroll(wrapperRef: React.RefObject<HTMLElement | null>) {
  const progressRef = useRef(0);
  const [stage, setStage] = useState(0);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    let raf = 0;

    function measure() {
      const el = wrapperRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const viewport = window.innerHeight;
      const total = rect.height - viewport;
      const traveled = Math.min(Math.max(-rect.top, 0), Math.max(total, 1));
      const next = total > 0 ? traveled / total : 0;
      progressRef.current = next;

      const nextStage = Math.min(STAGE_COUNT - 1, Math.floor(next * STAGE_COUNT));
      setStage((prev) => (prev === nextStage ? prev : nextStage));
    }

    function onScroll() {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(measure);
    }

    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [wrapperRef]);

  return { progressRef, stage, reducedMotion };
}
