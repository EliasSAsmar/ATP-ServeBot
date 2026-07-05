import type { Landmarks } from "./poseEngine";

/**
 * Canvas skeleton overlay for the live view (UI.md §3). Pure drawing — no
 * measurement implied anywhere; this is a tracking aid.
 */

// Bone list over MediaPipe Pose's 33 landmarks (torso + limbs + head sides).
const BONES: Array<[number, number]> = [
  [11, 12], // shoulders
  [11, 23],
  [12, 24],
  [23, 24], // hips
  [11, 13],
  [13, 15], // left arm
  [12, 14],
  [14, 16], // right arm
  [23, 25],
  [25, 27], // left leg
  [24, 26],
  [26, 28], // right leg
  [27, 31],
  [28, 32], // feet
  [15, 19],
  [16, 20], // hands
];

const JOINTS = [0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28];

const MIN_VISIBILITY = 0.5;

export function drawSkeleton(
  ctx: CanvasRenderingContext2D,
  landmarks: Landmarks,
  width: number,
  height: number,
  highlight = false,
): void {
  const visible = (i: number) => (landmarks[i]?.visibility ?? 1) >= MIN_VISIBILITY;

  // Phosphor-green stroke with a soft glow — matches the terminal theme.
  ctx.lineWidth = Math.max(2, width / 320);
  ctx.lineCap = "round";
  ctx.strokeStyle = highlight ? "rgba(160, 255, 200, 0.95)" : "rgba(90, 242, 154, 0.9)";
  ctx.shadowColor = "rgba(90, 242, 154, 0.45)";
  ctx.shadowBlur = 6;

  for (const [a, b] of BONES) {
    if (!landmarks[a] || !landmarks[b] || !visible(a) || !visible(b)) continue;
    ctx.beginPath();
    ctx.moveTo(landmarks[a].x * width, landmarks[a].y * height);
    ctx.lineTo(landmarks[b].x * width, landmarks[b].y * height);
    ctx.stroke();
  }

  ctx.fillStyle = highlight ? "rgba(190, 255, 220, 0.95)" : "rgba(198, 214, 205, 0.95)";
  const r = Math.max(3, width / 260);
  for (const i of JOINTS) {
    if (!landmarks[i] || !visible(i)) continue;
    ctx.beginPath();
    ctx.arc(landmarks[i].x * width, landmarks[i].y * height, r, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.shadowBlur = 0;
}
