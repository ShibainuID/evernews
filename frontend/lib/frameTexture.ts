import * as THREE from "three";

function roundedRectPath(ctx: CanvasRenderingContext2D, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(r, 0);
  ctx.arcTo(w, 0, w, h, r);
  ctx.arcTo(w, h, 0, h, r);
  ctx.arcTo(0, h, 0, 0, r);
  ctx.arcTo(0, 0, w, 0, r);
  ctx.closePath();
}

function drawChrome(ctx: CanvasRenderingContext2D, w: number, h: number, timestamp: string) {
  ctx.fillStyle = "rgba(255,60,60,0.9)";
  ctx.beginPath();
  ctx.arc(22, h - 24, 5, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = "rgba(255,255,255,0.92)";
  ctx.font = "600 20px 'Inter', system-ui, sans-serif";
  ctx.textBaseline = "middle";
  ctx.fillText(timestamp, 36, h - 24);

  ctx.strokeStyle = "rgba(255,255,255,0.14)";
  ctx.lineWidth = 2;
  roundedRectPath(ctx, w - 2, h - 2, 17);
  ctx.stroke();
}

/**
 * Renders a small "paused video frame" thumbnail from a real project image
 * (cover-fit, vignetted, with a timestamp + record indicator overlay), so
 * each trail panel reads as an actual freeze-frame rather than a placeholder.
 * The image loads async; the canvas starts as a dark plate and swaps in the
 * photo (calling `texture.needsUpdate = true`) once it's ready.
 *
 * `panBias` (-1..1) nudges the cover-fit crop per frame so five keyframes of
 * the *same* source clip read as distinct moments instead of an identical
 * repeated thumbnail.
 */
export function createFrameTexture(timestamp: string, imageSrc: string, panBias = 0): THREE.CanvasTexture {
  const w = 320;
  const h = 480;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d")!;

  ctx.save();
  roundedRectPath(ctx, w, h, 18);
  ctx.clip();
  ctx.fillStyle = "#0a1330";
  ctx.fillRect(0, 0, w, h);
  ctx.restore();
  drawChrome(ctx, w, h, timestamp);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;

  const img = new Image();
  img.onload = () => {
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    roundedRectPath(ctx, w, h, 18);
    ctx.clip();

    const scale = Math.max(w / img.width, h / img.height) * 1.12;
    const iw = img.width * scale;
    const ih = img.height * scale;
    const maxPanX = (iw - w) / 2;
    const maxPanY = (ih - h) / 2;
    ctx.drawImage(img, (w - iw) / 2 - maxPanX * panBias * 0.6, (h - ih) / 2 - maxPanY * panBias * 0.3, iw, ih);

    const vignette = ctx.createRadialGradient(w / 2, h / 2, h * 0.25, w / 2, h / 2, h * 0.7);
    vignette.addColorStop(0, "rgba(0,0,0,0)");
    vignette.addColorStop(1, "rgba(0,0,0,0.5)");
    ctx.fillStyle = vignette;
    ctx.fillRect(0, 0, w, h);
    ctx.restore();

    drawChrome(ctx, w, h, timestamp);
    texture.needsUpdate = true;
  };
  img.src = imageSrc;

  return texture;
}
