"use client";

import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { createFrameTexture } from "@/lib/frameTexture";

const CURVE_POINTS: [number, number, number][] = [
  [-2.4, 0.7, 3],
  [-0.6, -0.5, -1.5],
  [1.6, 0.9, -6],
  [-1, -0.7, -10.5],
  [1.9, 0.4, -15],
  [-0.2, -0.3, -19],
];

const FRAME_TS = [0.06, 0.28, 0.48, 0.68, 0.9];
const FRAME_LABELS = ["00:00", "00:03", "00:07", "00:11", "00:14"];
// All five keyframes are real photos of the same taxi/KRL crash case the
// copy walks through, so the trail shows genuine footage rather than one
// image repeated or unrelated stock photos.
const FRAME_IMAGES = [
  "/card-1.jpeg",
  "/card-2.jpeg",
  "/card-3.jpeg",
  "/card-4.jpeg",
  "/card-5.jpeg",
];
const MATCH_COLOR = new THREE.Color("#7fe8d4");
const CANDIDATE_GRAY = new THREE.Color("#3f4a5e");
// Frames float above the glowing thread rather than sitting centered on it,
// so the thread reads as a path running beside/below the cards instead of
// piercing through their middle.
const FRAME_Y_OFFSET = 0.95;

function smoothstep(edge0: number, edge1: number, x: number) {
  const t = Math.min(Math.max((x - edge0) / (edge1 - edge0), 0), 1);
  return t * t * (3 - 2 * t);
}

type Props = {
  /** 0..1 driven by scroll; ignored when `staticStage` is set. */
  progressRef?: React.MutableRefObject<number>;
  /** When set, renders a fixed pose for this stage instead of following progressRef (idle hero + reduced motion). */
  staticStage?: number;
  idleBreathing?: boolean;
  reducedMotion?: boolean;
  /** Called each frame with the current 0..1 split amount, so HTML overlays (not 3D world-space text, which can't be kept safely in frame) can track the "current vs. earliest match" reveal. */
  onSplit?: (split: number) => void;
};

export function TrailScene({ progressRef, staticStage, idleBreathing, reducedMotion, onSplit }: Props) {
  const curve = useMemo(() => new THREE.CatmullRomCurve3(CURVE_POINTS.map((p) => new THREE.Vector3(...p))), []);
  const cameraTarget = useRef(new THREE.Vector3());
  const groupRef = useRef<THREE.Group>(null);
  const threadRef = useRef<THREE.Mesh>(null);
  const glowRef = useRef<THREE.Mesh>(null);
  const pulseRef = useRef<THREE.Sprite>(null);
  const frameRefs = useRef<(THREE.Group | null)[]>([]);
  const materialRefs = useRef<(THREE.MeshBasicMaterial | null)[]>([]);
  const clock = useRef(0);
  const lastSplit = useRef(-1);

  const frameOffset = useMemo(() => new THREE.Vector3(0, FRAME_Y_OFFSET, 0), []);
  const origin = useMemo(() => curve.getPointAt(0.02).add(frameOffset), [curve, frameOffset]);
  const basePositions = useMemo(
    () => FRAME_TS.map((t) => curve.getPointAt(t).add(frameOffset)),
    [curve, frameOffset],
  );
  const textures = useMemo(
    () => FRAME_TS.map((_, i) => createFrameTexture(FRAME_LABELS[i], FRAME_IMAGES[i])),
    [],
  );
  const pulseTexture = useMemo(() => {
    const size = 128;
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    const ctx = canvas.getContext("2d")!;
    const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    g.addColorStop(0, "rgba(190,225,255,1)");
    g.addColorStop(0.35, "rgba(140,195,255,0.6)");
    g.addColorStop(1, "rgba(140,195,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
    return new THREE.CanvasTexture(canvas);
  }, []);

  // Camera travels a straight flythrough decoupled from the frames' curve so
  // it's guaranteed to stay in front of every frame (nearest frame z .. deepest frame z),
  // regardless of how the layout curve above gets tuned.
  const { camNearZ, camFarZ } = useMemo(() => {
    const zs = basePositions.map((p) => p.z);
    return { camNearZ: Math.max(...zs) + 6.5, camFarZ: Math.min(...zs) + 7.5 };
  }, [basePositions]);

  const tubeCore = useMemo(() => new THREE.TubeGeometry(curve, 220, 0.01, 8, false), [curve]);
  const tubeGlow = useMemo(() => new THREE.TubeGeometry(curve, 220, 0.05, 8, false), [curve]);

  useFrame((state, delta) => {
    clock.current += delta;
    const progress = staticStage !== undefined ? staticStage / 2 : (progressRef?.current ?? 0);

    const spread = smoothstep(0, 0.33, progress);
    const match = smoothstep(0.33, 0.62, progress);
    const split = smoothstep(0.66, 1, progress);

    if (onSplit && Math.abs(split - lastSplit.current) > 0.01) {
      lastSplit.current = split;
      onSplit(split);
    }

    frameRefs.current.forEach((group, i) => {
      if (!group) return;
      const pos = origin.clone().lerp(basePositions[i], staticStage !== undefined ? 1 : spread);

      // Keep the split drift small and on-axis so both ends stay safely
      // inside frame at any viewport width, since no 3D content should ever
      // depend on aspect ratio to stay visible.
      if (i === 0 && split > 0) pos.x -= split * 0.85;
      if (i === 4 && split > 0) pos.x += split * 0.85;

      group.position.copy(pos);
      group.lookAt(state.camera.position);

      const mat = materialRefs.current[i];
      if (mat) {
        if (i === 0 || i === 4) {
          mat.color.set("#ffffff");
          mat.opacity = 1;
        } else {
          mat.color.copy(CANDIDATE_GRAY).lerp(MATCH_COLOR, match);
          mat.opacity = 0.55 + match * 0.4 - split * 0.3;
        }
      }
    });

    const breathe = idleBreathing && !reducedMotion ? 0.12 * Math.sin(clock.current * 1.1) : 0;
    if (threadRef.current) {
      (threadRef.current.material as THREE.MeshBasicMaterial).opacity = 0.85 + match * 0.15;
    }
    if (glowRef.current) {
      (glowRef.current.material as THREE.MeshBasicMaterial).opacity = 0.35 + match * 0.25 + breathe;
    }

    if (pulseRef.current) {
      if (reducedMotion) {
        pulseRef.current.visible = false;
      } else {
        pulseRef.current.visible = true;
        const t = (clock.current * 0.06) % 1;
        const p = curve.getPointAt(t);
        pulseRef.current.position.copy(p);
        const fade = Math.sin(t * Math.PI);
        (pulseRef.current.material as THREE.SpriteMaterial).opacity = 0.5 + fade * 0.5;
      }
    }

    if (groupRef.current && idleBreathing && !reducedMotion) {
      groupRef.current.rotation.y = Math.sin(clock.current * 0.15) * 0.1;
    }

    // Camera sway stays modest so the trail never drifts toward the edge of
    // frame at narrower or wider aspect ratios.
    const camZ = camNearZ + (camFarZ - camNearZ) * progress;
    const camX = Math.sin(progress * Math.PI) * 0.35;
    // Base Y is nudged up by half the frame offset so the shifted-up cards
    // and the thread running below them both stay comfortably in view.
    const camY = 0.5 + FRAME_Y_OFFSET * 0.5 + Math.sin(progress * Math.PI * 2) * 0.1;
    const targetPos = new THREE.Vector3(camX * 0.25, camY * 0.5, camZ - 8);

    if (staticStage === undefined) {
      state.camera.position.lerp(new THREE.Vector3(camX, camY, camZ), 0.08);
      cameraTarget.current.lerp(targetPos, 0.08);
      state.camera.lookAt(cameraTarget.current);
    } else {
      state.camera.position.set(camX, camY, camZ);
      state.camera.lookAt(targetPos);
    }
  });

  return (
    <group ref={groupRef}>
      <mesh ref={glowRef} geometry={tubeGlow}>
        <meshBasicMaterial color="#7fb8ff" transparent opacity={0.4} depthWrite={false} blending={THREE.AdditiveBlending} />
      </mesh>
      <mesh ref={threadRef} geometry={tubeCore}>
        <meshBasicMaterial color="#eaf4ff" transparent opacity={0.95} toneMapped={false} />
      </mesh>

      <sprite ref={pulseRef} scale={[0.55, 0.55, 1]}>
        <spriteMaterial map={pulseTexture} transparent depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
      </sprite>

      {FRAME_TS.map((_, i) => (
        <group key={i} ref={(el) => (frameRefs.current[i] = el)}>
          <mesh>
            <planeGeometry args={[0.95, 1.42]} />
            <meshBasicMaterial
              ref={(el) => (materialRefs.current[i] = el)}
              map={textures[i]}
              transparent
              toneMapped={false}
            />
          </mesh>
        </group>
      ))}
    </group>
  );
}
