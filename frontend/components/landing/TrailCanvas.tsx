"use client";

import { Suspense } from "react";
import { Canvas } from "@react-three/fiber";
import { TrailScene } from "./TrailScene";

type Props = {
  progressRef?: React.MutableRefObject<number>;
  staticStage?: number;
  idleBreathing?: boolean;
  reducedMotion?: boolean;
  onSplit?: (split: number) => void;
  className?: string;
};

export function TrailCanvas({ progressRef, staticStage, idleBreathing, reducedMotion, onSplit, className }: Props) {
  return (
    <div className={className} aria-hidden="true">
      <Canvas
        dpr={[1, 1.75]}
        camera={{ fov: 45, near: 0.1, far: 50 }}
        gl={{ antialias: true, alpha: true }}
      >
        <Suspense fallback={null}>
          <TrailScene
            progressRef={progressRef}
            staticStage={staticStage}
            idleBreathing={idleBreathing}
            reducedMotion={reducedMotion}
            onSplit={onSplit}
          />
        </Suspense>
      </Canvas>
    </div>
  );
}
