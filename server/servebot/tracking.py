"""Ball / racket / person tracking + dense 2D pose over the serve window.

Phase-2 productionization of the proven ball-racket spike:

    full-frame YOLO (ultralytics, MPS) per frame  -> person / racket / small
    ball candidates -> static-ball suppression (grid occupancy) -> velocity-
    gated tracklet chaining -> pick the toss chain (largest upward travel)
    -> ROI-zoom re-detection to fill gaps -> gravity-fit scale (px per meter
    from the free-flight parabola) -> toss apex / contact (horizontal-velocity
    break, validated by racket-ball proximity) / toss placement / racket
    peak-speed proxy.

Plus a cheap dense 2D pose pass (YOLO-pose) across the same frames — used by
`servebot.temporal` for phase timing and kinetic-chain *timing* (a dense 3D
reconstruction of every frame would blow the per-serve latency budget).

Model choice is engineered against a ~60s/serve budget on M1 Pro (measured):
    yolo11m @ imgsz=1280 ~ 107 ms/frame, ball detected 40/40 spike frames
    yolo11x @ imgsz=1280 ~ 310 ms/frame (spike's model — 3x slower, same hits)
    yolo11n-pose @ 960   ~  23 ms/frame

torch / ultralytics / cv2 / numpy are imported lazily so importing this
module keeps the base (stub) app dependency-free.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("servebot.tracking")

# COCO class ids (the YOLO detection vocabulary).
COCO_PERSON, COCO_BALL, COCO_RACKET = 0, 32, 38

# Standard gravity — the anchor of the pixel->meter scale calibration.
G_M_S2 = 9.81

# Sanity floor for the fitted gravity term (px/s^2). Fits below this are
# rejected (ball not in free flight / too few points) — spike values were
# ~1300-1400 px/s^2 at 138 px/m.
MIN_G_PX_S2 = 300.0

# COCO-17 pose keypoint indices (YOLO-pose output).
P_NOSE = 0
P_L_SH, P_R_SH, P_L_EL, P_R_EL, P_L_WR, P_R_WR = 5, 6, 7, 8, 9, 10
P_L_HIP, P_R_HIP, P_L_KNEE, P_R_KNEE, P_L_ANK, P_R_ANK = 11, 12, 13, 14, 15, 16

# ---------------------------------------------------------------------------
# YOLO singletons — models stay resident per process (same policy as SAM3D).
# ---------------------------------------------------------------------------

_YOLO_CACHE: Dict[Tuple[str, str], Any] = {}
_YOLO_LOCK = threading.Lock()


def get_yolo(model_path: str, device: str) -> Any:
    """Load (once) and return an ultralytics YOLO model on `device`."""
    key = (model_path, device)
    with _YOLO_LOCK:
        model = _YOLO_CACHE.get(key)
        if model is None:
            try:
                from ultralytics import YOLO
            except ImportError as exc:  # pragma: no cover — env guard
                raise RuntimeError(
                    "SERVEBOT_PIPELINE=sam3d tracking requires ultralytics: "
                    "pip install -r server/requirements-ml.txt"
                ) from exc
            t0 = time.perf_counter()
            model = YOLO(model_path)
            _YOLO_CACHE[key] = model
            log.info("YOLO %s loaded in %.1fs", model_path, time.perf_counter() - t0)
        return model


# ---------------------------------------------------------------------------
# Data carriers
# ---------------------------------------------------------------------------


@dataclass
class Tracks:
    """Raw per-frame tracking output over the analysis window."""

    fps: float
    width: int
    height: int
    frame_ids: List[int]                    # absolute clip frame indices
    person: List[Optional[List[float]]]     # xyxy(+conf) per window frame
    racket: List[Optional[List[float]]]
    racket_src: List[Optional[str]]         # det | interp | None
    ball_chain: Optional[dict]              # {"frames": abs ids, "boxes", "src"}
    pose_xy: Any = None                     # np (N,17,2) or None
    pose_conf: Any = None                   # np (N,17) or None
    ms_detect_per_frame: float = 0.0
    ms_pose_per_frame: float = 0.0


@dataclass
class BallAnalysis:
    """Gravity-calibrated serve geometry derived from the toss chain."""

    px_per_m: float
    scale_method: str                        # gravity_fit | person_height_prior
    ground_y: float                          # px, image y of the feet line
    player_height_px: float
    direction: float                         # +1 serving toward image +x
    # toss chain samples (absolute frame ids -> px centers)
    frames: List[int] = field(default_factory=list)
    cx: List[float] = field(default_factory=list)
    cy: List[float] = field(default_factory=list)
    in_flight: List[bool] = field(default_factory=list)
    # events (absolute frames / seconds since clip start)
    release_frame: int = 0
    apex_t_s: float = 0.0
    apex_height_m: float = 0.0
    apex_x_px: float = 0.0
    contact_frame: int = 0
    contact_t_s: float = 0.0
    contact_height_m: float = 0.0
    contact_confidence: float = 0.5
    # toss placement (vs body center x before release)
    offset_forward_m: float = 0.0
    racket_peak_speed_m_s: Optional[float] = None
    racket_points: List[Tuple[int, float, float]] = field(default_factory=list)


def _ctr(b: List[float]) -> Tuple[float, float]:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


# ---------------------------------------------------------------------------
# Detection + chaining (adapted from the spike's detect_track.py)
# ---------------------------------------------------------------------------


def run_tracking(
    frames_bgr: List[Any],
    frame_ids: List[int],
    fps: float,
    det_model: Any,
    pose_model: Any,
    device: str,
) -> Tracks:
    """Full tracking pass over the (bounded) analysis window."""
    import numpy as np

    n = len(frames_bgr)
    h, w = frames_bgr[0].shape[:2]
    max_ball_px = max(20.0, 45.0 * w / 1280.0)  # spike's 45px at 1280w

    person: List[Optional[List[float]]] = [None] * n
    racket: List[Optional[List[float]]] = [None] * n
    rawball: List[List[List[float]]] = [[] for _ in range(n)]

    t0 = time.perf_counter()
    for i, img in enumerate(frames_bgr):
        r = det_model.predict(
            img, device=device, imgsz=1280, conf=0.04,
            classes=[COCO_PERSON, COCO_BALL, COCO_RACKET], verbose=False,
        )[0]
        bestp = bestr = None
        for b in r.boxes:
            c, conf = int(b.cls), float(b.conf)
            xy = [float(v) for v in b.xyxy[0]]
            if c == COCO_PERSON:
                area = (xy[2] - xy[0]) * (xy[3] - xy[1])
                if bestp is None or area > bestp[1]:
                    bestp = (xy + [conf], area)
            elif c == COCO_RACKET:
                if bestr is None or conf > bestr[4]:
                    bestr = xy + [conf]
            elif c == COCO_BALL:
                bw, bh = xy[2] - xy[0], xy[3] - xy[1]
                if bw < max_ball_px and bh < max_ball_px:
                    rawball[i].append(xy + [conf])
        if bestp:
            person[i] = bestp[0]
        if bestr:
            racket[i] = bestr
    ms_det = (time.perf_counter() - t0) / max(n, 1) * 1000.0

    # ---- static-ball suppression (grid occupancy over the window) ----
    cell = 18.0 * w / 1280.0
    occ: Dict[Tuple[int, int], int] = {}
    for i in range(n):
        seen = set()
        for b in rawball[i]:
            c = _ctr(b)
            key = (int(c[0] // cell), int(c[1] // cell))
            if key not in seen:
                occ[key] = occ.get(key, 0) + 1
                seen.add(key)

    def is_static(c: Tuple[float, float]) -> bool:
        kx, ky = int(c[0] // cell), int(c[1] // cell)
        tot = max(
            occ.get((kx + dx, ky + dy), 0) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
        )
        return tot > 0.22 * n

    moving = [[b for b in rawball[i] if not is_static(_ctr(b))] for i in range(n)]

    # ---- velocity-gated tracklet chaining ----
    tracklets: List[dict] = []
    active: List[dict] = []
    for i in range(n):
        dets = sorted(moving[i], key=lambda b: -b[4])
        used = [False] * len(dets)
        for tr in active:
            pred = (
                tr["pos"][0] + tr["vel"][0] * (tr["miss"] + 1),
                tr["pos"][1] + tr["vel"][1] * (tr["miss"] + 1),
            )
            gate = 46 + 34 * tr["miss"] + 0.9 * float(np.hypot(*tr["vel"]))
            best = None
            for k, d in enumerate(dets):
                if used[k]:
                    continue
                c = _ctr(d)
                e = float(np.hypot(c[0] - pred[0], c[1] - pred[1]))
                if e < gate and (best is None or e < best[1]):
                    best = (k, e)
            if best is not None:
                k = best[0]
                used[k] = True
                c = _ctr(dets[k])
                dt = tr["miss"] + 1
                nv = ((c[0] - tr["pos"][0]) / dt, (c[1] - tr["pos"][1]) / dt)
                a = 0.65
                tr["vel"] = (a * nv[0] + (1 - a) * tr["vel"][0], a * nv[1] + (1 - a) * tr["vel"][1])
                tr["pos"] = c
                tr["miss"] = 0
                tr["frames"].append(i)
                tr["boxes"].append(dets[k])
            else:
                tr["miss"] += 1
        active = [t for t in active if t["miss"] <= 6]
        for k, d in enumerate(dets):
            if used[k]:
                continue
            active.append({"frames": [i], "boxes": [d], "pos": _ctr(d), "vel": (0.0, 0.0), "miss": 0})
            tracklets.append(active[-1])

    chains = [t for t in tracklets if len(t["frames"]) >= 4]
    # Toss chain = biggest upward travel (scaled from the spike's 90px @720h).
    min_rise = 90.0 * h / 720.0
    toss = [
        t for t in chains
        if (_ctr(t["boxes"][0])[1] - min(_ctr(b)[1] for b in t["boxes"])) > min_rise
    ]
    ball_chain: Optional[dict] = None
    if toss:
        # v1: one serve per clip — take the chain with the largest rise.
        toss.sort(key=lambda t: -(_ctr(t["boxes"][0])[1] - min(_ctr(b)[1] for b in t["boxes"])))
        t = toss[0]
        _roi_fill(t, frames_bgr, det_model, device, w, h, max_ball_px)
        ball_chain = {
            "frames": [frame_ids[j] for j in t["frames"]],
            "boxes": t["boxes"],
            "src": t["src"],
        }

    # ---- racket / person gap interpolation ----
    def interp(track: List[Optional[List[float]]], max_gap: int) -> Tuple[list, list]:
        src = ["det" if b is not None else None for b in track]
        idx = [i for i, b in enumerate(track) if b is not None]
        for a, b in zip(idx, idx[1:]):
            if 1 < b - a <= max_gap:
                for j in range(a + 1, b):
                    u = (j - a) / (b - a)
                    track[j] = [track[a][k] * (1 - u) + track[b][k] * u for k in range(4)] + [0.0]
                    src[j] = "interp"
        return track, src

    racket, racket_src = interp(racket, 8)
    person, _ = interp(person, 6)

    # ---- dense 2D pose (largest person per frame) ----
    t0 = time.perf_counter()
    pose_xy = np.full((n, 17, 2), np.nan)
    pose_conf = np.zeros((n, 17))
    for i, img in enumerate(frames_bgr):
        r = pose_model.predict(img, device=device, imgsz=960, conf=0.25, verbose=False)[0]
        if r.keypoints is None or r.boxes is None or len(r.boxes) == 0:
            continue
        areas = [
            float((b.xyxy[0][2] - b.xyxy[0][0]) * (b.xyxy[0][3] - b.xyxy[0][1]))
            for b in r.boxes
        ]
        k = int(np.argmax(areas))
        kxy = r.keypoints.xy[k].cpu().numpy()
        pose_xy[i] = kxy
        if r.keypoints.conf is not None:
            pose_conf[i] = r.keypoints.conf[k].cpu().numpy()
    ms_pose = (time.perf_counter() - t0) / max(n, 1) * 1000.0

    log.info(
        "tracking: %d frames, det %.0f ms/f, pose %.0f ms/f, ball chain %s",
        n, ms_det, ms_pose,
        f"{len(ball_chain['frames'])} pts" if ball_chain else "none",
    )
    return Tracks(
        fps=fps, width=w, height=h, frame_ids=list(frame_ids),
        person=person, racket=racket, racket_src=racket_src,
        ball_chain=ball_chain, pose_xy=pose_xy, pose_conf=pose_conf,
        ms_detect_per_frame=ms_det, ms_pose_per_frame=ms_pose,
    )


def _roi_fill(
    chain: dict, frames_bgr: List[Any], model: Any, device: str,
    w: int, h: int, max_ball_px: float,
) -> None:
    """Fill interior gaps and extend the toss chain with ROI re-detection."""
    import numpy as np

    n = len(frames_bgr)

    def roi_detect(i: int, pred: Tuple[float, float], half: int = 170):
        x, y = pred
        x0, y0 = int(max(0, x - half)), int(max(0, y - half))
        x1, y1 = int(min(w, x + half)), int(min(h, y + half))
        if x1 - x0 < 40 or y1 - y0 < 40:
            return None
        r = model.predict(
            frames_bgr[i][y0:y1, x0:x1], device=device, imgsz=640,
            conf=0.008, classes=[COCO_BALL], verbose=False,
        )[0]
        best = None
        for b in r.boxes:
            xy = [float(v) for v in b.xyxy[0]]
            if xy[2] - xy[0] > max_ball_px or xy[3] - xy[1] > max_ball_px:
                continue
            gb = [xy[0] + x0, xy[1] + y0, xy[2] + x0, xy[3] + y0]
            c = _ctr(gb)
            e = float(np.hypot(c[0] - x, c[1] - y))
            if e < 80 and (best is None or e < best[1]):
                best = ((gb, float(b.conf)), e)
        return best[0] if best else None

    fr, bx = chain["frames"], chain["boxes"]
    newf: List[int] = []
    newb: List[List[float]] = []
    news: List[str] = []
    for a in range(len(fr) - 1):
        newf.append(fr[a]); newb.append(bx[a]); news.append("det")
        gap = fr[a + 1] - fr[a]
        if 1 < gap <= 8:
            ca, cb = _ctr(bx[a]), _ctr(bx[a + 1])
            for j in range(fr[a] + 1, fr[a + 1]):
                u = (j - fr[a]) / gap
                pred = (ca[0] * (1 - u) + cb[0] * u, ca[1] * (1 - u) + cb[1] * u)
                got = roi_detect(j, pred)
                if got:
                    newf.append(j); newb.append(list(got[0]) + [got[1]]); news.append("roi")
                else:
                    s = 10.0
                    newf.append(j)
                    newb.append([pred[0] - s, pred[1] - s, pred[0] + s, pred[1] + s, 0.0])
                    news.append("interp")
    newf.append(fr[-1]); newb.append(bx[-1]); news.append("det")

    # extend backward (toward release) and forward (past contact) with ROI
    for direc in (-1, 1):
        misses = 0
        while misses < 3:
            if direc == -1:
                j = newf[0] - 1
                if j < 0:
                    break
                c1, c2 = _ctr(newb[0]), _ctr(newb[min(2, len(newb) - 1)])
                dfr = newf[min(2, len(newf) - 1)] - newf[0]
            else:
                j = newf[-1] + 1
                if j >= n:
                    break
                c1, c2 = _ctr(newb[-1]), _ctr(newb[max(-3, -len(newb))])
                dfr = newf[-1] - newf[max(-3, -len(newf))]
            v = ((c1[0] - c2[0]) / max(dfr, 1), (c1[1] - c2[1]) / max(dfr, 1))
            pred = (c1[0] + v[0], c1[1] + v[1])
            got = roi_detect(j, pred, half=150)
            if got:
                if direc == -1:
                    newf.insert(0, j); newb.insert(0, list(got[0]) + [got[1]]); news.insert(0, "roi")
                else:
                    newf.append(j); newb.append(list(got[0]) + [got[1]]); news.append("roi")
                misses = 0
            else:
                misses += 1
    chain["frames"], chain["boxes"], chain["src"] = newf, newb, news


# ---------------------------------------------------------------------------
# Ball-arc analysis (adapted from the spike's analyze.py) — pure math.
# ---------------------------------------------------------------------------


def fit_toss_parabola(t_s, y_px):
    """Quadratic fit y = a t^2 + b t + c over the free flight (y down = +).

    Returns (g_px_s2, t_apex_s, y_apex_px) — g is 2a. Pure math; unit-tested
    torch-free.
    """
    import numpy as np

    coef = np.polyfit(np.asarray(t_s, float), np.asarray(y_px, float), 2)
    g_px = 2.0 * coef[0]
    t_apex = float(-coef[1] / (2.0 * coef[0]))
    y_apex = float(np.polyval(coef, t_apex))
    return float(g_px), t_apex, y_apex


def analyze_ball_arc(
    tracks: Tracks,
    clip_fps: float,
    fallback_player_height_m: float = 1.75,
) -> Optional[BallAnalysis]:
    """Toss apex / contact / placement / scale from the tracked ball chain.

    Contact = the horizontal-velocity break of the ball track (toss drifts at
    a few px/frame; post-impact flight is an order of magnitude faster),
    validated by racket-ball proximity. Scale = gravity fit on the toss free
    flight; falls back to a person-height prior when the fit is degenerate.
    Returns None when there is no usable toss chain.

    NOTE on time bases: chain `frames` are ABSOLUTE clip frame indices, so
    all chain math uses `clip_fps`. Racket speed is differentiated over the
    (possibly strided) window samples, so it uses the window's effective fps.
    """
    import numpy as np

    ch = tracks.ball_chain
    if not ch or len(ch["frames"]) < 6:
        return None
    fps = clip_fps
    id_to_win = {fid: i for i, fid in enumerate(tracks.frame_ids)}

    fr = np.array(ch["frames"], dtype=float)          # absolute frame ids
    cx = np.array([_ctr(b)[0] for b in ch["boxes"]])
    cy = np.array([_ctr(b)[1] for b in ch["boxes"]])
    vy = np.gradient(cy, fr)
    dx = np.diff(cx) / np.diff(fr)

    # ---- contact: horizontal-velocity break (px per abs frame, scaled from
    # the spike's proven 25 px/frame @ 1280w/25fps) ----
    thr_px_f = 25.0 * (tracks.width / 1280.0) * (25.0 / fps)
    i_contact = None
    for i in range(3, len(dx)):
        if abs(dx[i]) > thr_px_f:
            i_contact = i + 1
            break
    if i_contact is None:
        i_contact = len(fr) - 1
    i_c = max(i_contact - 1, 1)          # last point on the toss path
    f_contact = int(fr[i_contact])
    direction = 1.0 if (dx[i_contact - 1] if i_contact - 1 < len(dx) else 0.0) >= 0 else -1.0

    # ---- release: peak upward speed before the apex ----
    pre = vy[: max(i_c - 2, 1)]
    i_rel = int(np.argmin(pre)) if len(pre) else 0
    ff = slice(i_rel, i_c + 1)
    if (i_c + 1 - i_rel) < 4:
        return None

    g_px, t_apex, y_apex = fit_toss_parabola(fr[ff] / fps, cy[ff])
    x_apex = float(np.interp(t_apex, fr / fps, cx))

    # ---- player reference + scale ----
    feet, heights, pcx = [], [], []
    for p in tracks.person:
        if p:
            feet.append(p[3])
            heights.append(p[3] - p[1])
            pcx.append((p[0] + p[2]) / 2.0)
        else:
            pcx.append(np.nan)
    if not feet:
        return None
    ground_y = float(np.median(feet))
    player_h_px = float(np.median(heights))
    if g_px > MIN_G_PX_S2:
        m_per_px = G_M_S2 / g_px
        method = "gravity_fit"
    else:
        # degenerate parabola — anchor scale on an assumed player height
        m_per_px = fallback_player_height_m / max(player_h_px, 1.0)
        method = "person_height_prior"

    # ---- racket peak-speed proxy around contact ----
    rc = np.array([_ctr(r) if r else (np.nan, np.nan) for r in tracks.racket])
    racket_peak = None
    racket_points: List[Tuple[int, float, float]] = []
    if np.isfinite(rc[:, 0]).sum() >= 5:
        # px/s over the window samples (uniform spacing = 1/effective fps)
        rspd = np.hypot(np.gradient(rc[:, 0]), np.gradient(rc[:, 1])) * tracks.fps
        wi = id_to_win.get(f_contact)
        if wi is not None:
            lo, hi = max(0, wi - 5), min(len(rspd), wi + 4)
            seg = rspd[lo:hi]
            if np.isfinite(seg).any():
                racket_peak = float(np.nanmax(seg) * m_per_px)
        for i, fid in enumerate(tracks.frame_ids):
            if np.isfinite(rc[i, 0]):
                racket_points.append((fid, float(rc[i, 0]), float(rc[i, 1])))

    # ---- toss placement: apex forward of body center at release ----
    wi_rel = id_to_win.get(int(fr[i_rel]), 0)
    body = np.array(pcx[max(0, wi_rel - 10): wi_rel + 1], dtype=float)
    body_x = float(np.nanmedian(body)) if np.isfinite(body).any() else float(np.nanmedian(pcx))
    fwd_apex_m = (x_apex - body_x) * direction * m_per_px

    # ---- contact confidence: is the racket at the ball at the break? ----
    conf = 0.5
    wi_c = id_to_win.get(f_contact)
    if wi_c is not None and tracks.racket[wi_c] is not None:
        b = tracks.racket[wi_c]
        bxc, byc = float(cx[i_c]), float(cy[i_c])
        margin = 40.0 * tracks.width / 1280.0
        inside = (b[0] - margin) <= bxc <= (b[2] + margin) and (b[1] - margin) <= byc <= (b[3] + margin)
        conf = 0.9 if inside else 0.6

    in_flight = [i_rel <= i <= i_c for i in range(len(fr))]
    return BallAnalysis(
        px_per_m=1.0 / m_per_px,
        scale_method=method,
        ground_y=ground_y,
        player_height_px=player_h_px,
        direction=direction,
        frames=[int(v) for v in fr],
        cx=[float(v) for v in cx],
        cy=[float(v) for v in cy],
        in_flight=in_flight,
        release_frame=int(fr[i_rel]),
        apex_t_s=t_apex,
        apex_height_m=float((ground_y - y_apex) * m_per_px),
        apex_x_px=x_apex,
        contact_frame=f_contact,
        contact_t_s=f_contact / fps,
        contact_height_m=float((ground_y - cy[i_c]) * m_per_px),
        contact_confidence=conf,
        offset_forward_m=float(fwd_apex_m),
        racket_peak_speed_m_s=racket_peak,
        racket_points=racket_points,
    )
