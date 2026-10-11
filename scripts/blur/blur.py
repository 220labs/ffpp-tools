#!/usr/bin/env python3
"""Blur people who did not agree to be in a film: find faces, pick them by number, blur, prove it.

  bin/tool blur scan  <clip> [--every 0.5]                 faces → numbered tracks + a contact sheet
  bin/tool blur apply <clip> [--tracks 2,5] [--box x,y,w,h@t1-t2 …] [--who … --reason … --consent …] [--drop]
  bin/tool blur check <clip>                               the result, track by track, in sentences

scan samples a frame every --every seconds, finds faces with OpenCV's YuNet detector, links the
detections into tracks by overlap, and writes $WORK/blur/<stem>.tracks.json and a numbered
contact sheet $WORK/blur/<stem>-sheet.jpg: one row per track, the whole frame with its box, then
the frames where that face is largest; each row gives the largest box in source pixels. Pick
tracks from the sheet by the production's consent list, never by guess.
apply blurs the chosen tracks (box interpolated between detections, padded, Gaussian) and any
manual box (x,y,w,h in the source's upright, full-size pixels, the space of the boxes in
tracks.json, never the sheet's scaled picture; @t1-t2 seconds; no @ means the whole clip) for a
body, a badge or a screen the detector cannot see. It writes <stem>-blur.mp4 beside the source
(.mov when the audio is PCM), the picture re-encoded (x264, CRF 16) and the audio stream copied
bit-for-bit. Every apply starts from the source: give every track and box each time; one the
last apply blurred and this one leaves out is refused, unless --drop says so. A face is known
by its detections, not its number: after a re-scan renumbers the tracks, the refusal names each
earlier face by its new number, or by its probable one when the scan found it differently (then
re-pick with --drop and no --who, and add new people on the next apply). --who/--reason/--consent
append to $WORK/blur/log.yaml, the only place that names anyone, with the tracks and boxes this
apply adds (not the ones it repeats), each track with where it first appears; give them on the
apply that first blurs that person.
check samples the blurred clip twice as densely as the scan (at its frames and between them,
where boxes are interpolated), runs the detector again near every chosen track and compares the
detail inside every box with the source, then prints one sentence per track and box and a
second sheet, $WORK/blur/<stem>-check.jpg. Exit 1 when a face is still readable or a box was
never measured. It proves a box is blurred, not that it covers the person: look at the sheet.

$WORK (or --work) is the production's work folder, outside every git checkout (refused inside
one: the log names people, the sheets show them); clips resolve against --media. Records are
named by the clip's stem, so two clips of one name need two --work folders.
The model is downloaded by scripts/blur/fetch-model.sh;
$BLUR_MODEL overrides where it is read from. HDR (HLG/PQ) sources come out SDR without tone
mapping (this ffmpeg has no zscale): check the colours, or blur an SDR export instead.
"""
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from lib import cli, paths  # noqa: E402

MODEL = "face_detection_yunet_2023mar.onnx"
SCORE = 0.6        # YuNet's own demo threshold; lower finds more profiles and more false faces
MAX_SIDE = 1920    # detection runs on a copy at most this wide: 4x faster on 4K, but a face that is
                   # small at full size is halved too and may be missed (unmeasured): scan a crowd
                   # at 4K with BLUR_MAX_SIDE=3840 and compare the sheets
MAX_SIDE = int(os.environ.get("BLUR_MAX_SIDE", MAX_SIDE))
GAP = 3            # a track survives this many samples without a detection (a turned head, a hand)
CELL = 180         # sheet crop size, px
THUMB = 320        # sheet whole-frame width, px
CROPS = 5          # crops per sheet row
SHARP = 0.35       # check: a box keeping more than this share of its source detail is still readable


def cv():
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")   # its DNN backend warns on every load
    try:
        import cv2
        import numpy
    except ImportError:
        raise SystemExit("blur: needs OpenCV — pip3 install --user opencv-python")
    return cv2, numpy


def model_path():
    """$BLUR_MODEL, else scripts/blur/models/ in this checkout, else in its main checkout (a
    worktree has no gitignored files of its own until it fetches them)."""
    if os.environ.get("BLUR_MODEL"):
        return os.environ["BLUR_MODEL"]
    here = os.path.join(os.path.dirname(os.path.realpath(__file__)), "models", MODEL)
    for p in (here, paths.main_checkout(here)):
        if os.path.isfile(p):
            return p
    raise SystemExit(f"blur: no face model at {here} — run: sh scripts/blur/fetch-model.sh")


# ---- video in and out ------------------------------------------------------------------------

def probe(clip):
    j = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", clip],
        check=True, capture_output=True, text=True).stdout)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise SystemExit(f"blur: {clip} has no video stream")
    a = [s for s in j["streams"] if s["codec_type"] == "audio"]
    rot = 0
    for sd in v.get("side_data_list", []):
        if "rotation" in sd:   # the display matrix; other entries (HDR, Dolby Vision) follow it
            rot = int(sd["rotation"] or 0)
    rot = rot or int(v.get("tags", {}).get("rotate", 0) or 0)
    w, h = v["width"], v["height"]
    if abs(rot) % 180 == 90:   # ffmpeg turns the picture upright on decode; so do our coordinates
        w, h = h, w
    n, d = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "25/1").split("/")
    rate = f"{n}/{d}" if float(d) and float(n) else v.get("r_frame_rate", "25/1")
    return {"w": w, "h": h, "rate": rate, "fps": eval_rate(rate),
            "duration": float(j["format"].get("duration") or v.get("duration") or 0),
            "audio": [s["codec_name"] for s in a],
            "hdr": v.get("color_transfer") in ("arib-std-b67", "smpte2084"),
            "created": j["format"].get("tags", {}).get("creation_time", ""),
            "size": os.path.getsize(clip)}


def eval_rate(r):
    n, d = r.split("/")
    return float(n) / float(d)


def frames(clip, w, h, fps_filter):
    """Decoded frames, upright, as BGR arrays, at the rate the fps filter sets."""
    _, np = cv()
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", clip, "-map", "0:v:0", "-vf", fps_filter,
                          "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    size = w * h * 3
    try:
        while True:
            b = p.stdout.read(size)
            if len(b) < size:
                break
            yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


def samples(clip, info, every):
    """(time, frame) every `every` seconds from 0: the grid scan and check share. round=up makes
    sample k the frame at k*every or the one just before; ffmpeg's default (near) hands back the
    frame before (k+0.5)*every, which put every box half a sample early (measured on a clip whose
    brightness is its frame number, ffmpeg 9.0.2)."""
    for k, f in enumerate(frames(clip, info["w"], info["h"], f"fps=1/{every}:round=up")):
        yield round(k * every, 3), f


# ---- detection and tracks --------------------------------------------------------------------

class Detector:
    def __init__(self, w, h, score=SCORE):
        cv2, _ = cv()
        self.scale = min(1.0, MAX_SIDE / max(w, h))
        self.size = (max(1, round(w * self.scale)), max(1, round(h * self.scale)))
        self.net = cv2.FaceDetectorYN.create(model_path(), "", self.size, score, 0.3, 5000)

    def __call__(self, frame):
        cv2, _ = cv()
        img = frame if self.scale == 1.0 else cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA)
        _, faces = self.net.detect(img)
        out = []
        for f in faces if faces is not None else []:
            x, y, w, h = (float(v) / self.scale for v in f[:4])
            out.append(([round(x), round(y), round(w), round(h)], round(float(f[-1]), 3)))
        return out


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def gap(a, b):
    """Centre distance in units of the larger box's side: 0 is the same place, 1 is one face over."""
    ca = (a[0] + a[2] / 2, a[1] + a[3] / 2)
    cb = (b[0] + b[2] / 2, b[1] + b[3] / 2)
    return ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5 / max(a[2], a[3], b[2], b[3], 1)


def near(a, b, reach):
    """Within `reach` face-widths of each other and of a like size (half to twice): the same face
    after a fast move. IoU alone cannot see it: two same-size boxes stop overlapping once their
    centres are one width apart, a walk across a medium shot in half a second."""
    ratio = max(a[2], a[3]) / max(b[2], b[3], 1)
    return gap(a, b) < reach and 0.5 <= ratio <= 2


LINK_REACH = 1.5   # face-widths a face may move per sample and stay one track


def link(tracks, k, t, dets):
    """Greedy: each detection goes to the live track it overlaps most, else the nearest one within
    reach (more reach the more samples the track has missed); the rest start new tracks.
    Returns the track each detection went to."""
    live = [tr for tr in tracks if k - tr["last"] <= GAP]
    pairs = []
    for i, (box, _) in enumerate(dets):
        for tr in live:
            prev = tr["detections"][-1]["box"]
            o = iou(box, prev)
            if o > 0.15 or near(box, prev, LINK_REACH * (k - tr["last"])):
                pairs.append((o, gap(box, prev), i, tr))
    taken, used, owner = set(), set(), {}
    for o, _, i, tr in sorted(pairs, key=lambda p: (-p[0], p[1])):
        if i in taken or id(tr) in used:
            continue
        taken.add(i)
        used.add(id(tr))
        owner[i] = tr
    for i, (box, score) in enumerate(dets):
        tr = owner.get(i)
        if tr is None:
            tr = {"id": len(tracks) + 1, "detections": []}
            tracks.append(tr)
            owner[i] = tr
        tr["detections"].append({"t": t, "box": box, "score": score})
        tr["last"] = k
    return owner


# ---- the contact sheet -----------------------------------------------------------------------

def crop(frame, box, pad=0.6):
    cv2, _ = cv()
    x, y, w, h = box
    s = max(w, h) * (1 + 2 * pad)
    cx, cy = x + w / 2, y + h / 2
    H, W = frame.shape[:2]
    x0, y0 = max(0, int(cx - s / 2)), max(0, int(cy - s / 2))
    x1, y1 = min(W, int(cx + s / 2)), min(H, int(cy + s / 2))
    c = frame[y0:y1, x0:x1]
    return cv2.resize(c, (CELL, CELL), interpolation=cv2.INTER_AREA) if c.size else None


def thumb(frame, box, label):
    cv2, _ = cv()
    H, W = frame.shape[:2]
    s = THUMB / W
    im = cv2.resize(frame, (THUMB, max(1, round(H * s))), interpolation=cv2.INTER_AREA)
    x, y, w, h = (round(v * s) for v in box)
    cv2.rectangle(im, (x, y), (x + w, y + h), (0, 255, 255), 2)
    cv2.putText(im, label, (max(0, x), max(14, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
    return im


def sheet(rows, out, empty_note):
    """rows: [(caption, thumb image, [(crop image, label)…])] → one JPEG, a row per track."""
    cv2, np = cv()
    cap = 26
    width = THUMB + CROPS * CELL
    if not rows:
        im = np.full((60, width, 3), 32, np.uint8)
        cv2.putText(im, empty_note, (10, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imwrite(out, im, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return
    blocks = []
    for caption, th, crops in rows:
        hh = max(CELL, th.shape[0] if th is not None else 0)
        b = np.full((cap + hh, width, 3), 32, np.uint8)
        cv2.putText(b, caption, (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        if th is not None:
            b[cap:cap + th.shape[0], :THUMB] = th
        for i, (c, label) in enumerate(crops[:CROPS]):
            if c is None:
                continue
            x = THUMB + i * CELL
            b[cap:cap + CELL, x:x + CELL] = c
            cv2.putText(b, label, (x + 4, cap + CELL - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(b, label, (x + 4, cap + CELL - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        blocks.append(b)
    cv2.imwrite(out, np.vstack(blocks), [cv2.IMWRITE_JPEG_QUALITY, 90])


# ---- where things go -------------------------------------------------------------------------

class Work:
    def __init__(self, work, clip):
        if not work:
            raise SystemExit("blur: set $WORK (or --work) to the production's work folder outside git; "
                             "tracks, sheets and the log go to $WORK/blur/")
        root = os.path.abspath(os.path.expanduser(work))
        # The log names people and the sheets show their faces: a folder git can see would let
        # one `git add -A` publish both, and a production's .gitignore does not cover them.
        probe_dir = root
        while not os.path.isdir(probe_dir):
            probe_dir = os.path.dirname(probe_dir)
        inside = subprocess.run(["git", "-C", probe_dir, "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True).stdout.strip() == "true"
        if inside:
            raise SystemExit(f"blur: $WORK {root} is inside a git checkout — the log names people and the "
                             f"sheets show faces; use a folder outside every repo")
        self.dir = os.path.join(root, "blur")
        os.makedirs(self.dir, exist_ok=True)
        self.stem = os.path.splitext(os.path.basename(clip))[0]

    def owned(self, path, clip, size):
        """Refuse a record another clip with this stem wrote (C0001.MP4 from two cards)."""
        if os.path.isfile(path):
            rec = json.load(open(path))
            src = rec.get("clip") or rec.get("source")
            if rec.get("size") != size and src != os.path.abspath(clip):
                raise SystemExit(f"blur: {path} belongs to {src}, another clip with this name — give this "
                                 f"one its own --work")

    def __getattr__(self, kind):
        names = {"tracks": ".tracks.json", "sheet": "-sheet.jpg", "applied": ".applied.json",
                 "checksheet": "-check.jpg"}
        if kind not in names:
            raise AttributeError(kind)
        return os.path.join(self.dir, self.stem + names[kind])

    @property
    def log(self):
        return os.path.join(self.dir, "log.yaml")


def rel(p):
    return os.path.relpath(p) if not os.path.relpath(p).startswith("..") else p


# ---- scan ------------------------------------------------------------------------------------

def scan(clip, work, every):
    info = probe(clip)
    if info["hdr"]:
        print("blur: HDR source — detection runs on an untone-mapped SDR picture; faces may be missed in "
              "the dark, check the sheet", file=sys.stderr)
    work.owned(work.tracks, clip, info["size"])
    work.owned(work.applied, clip, info["size"])
    if os.path.isfile(work.applied) and json.load(open(work.applied)).get("chosen"):
        print(f"blur: an earlier apply blurred tracks of this clip — this scan may number them differently; "
              f"the next apply names each earlier face by its new number, or its probable one when this scan "
              f"found it differently", file=sys.stderr)
    det = Detector(info["w"], info["h"])
    # Only the CROPS largest crops per track are kept: an hour of a crowd is tens of thousands
    # of detections, and a crop of each would hold gigabytes for a sheet that shows five a row.
    tracks, best, kept, n = [], {}, {}, 0
    for k, (t, f) in enumerate(samples(clip, info, every)):
        n += 1
        dets = det(f)
        owner = link(tracks, k, t, dets)
        for i, tr in owner.items():
            d = tr["detections"][-1]
            area = d["box"][2] * d["box"][3]
            ks = kept.setdefault(tr["id"], [])
            if len(ks) < CROPS or area > ks[-1][0]:
                ks.append((area, d["t"], crop(f, d["box"])))
                ks.sort(key=lambda c: -c[0])
                del ks[CROPS:]
            if area >= best.get(tr["id"], (0, None))[0]:
                best[tr["id"]] = (area, thumb(f, d["box"], f"#{tr['id']}"), d["box"])
    rows = []
    for tr in tracks:
        ds = tr["detections"]
        _, th, bb = best[tr["id"]]
        cap = (f"#{tr['id']}  {len(ds)} frame{'s' * (len(ds) != 1)}, {ds[0]['t']:.1f}-{ds[-1]['t']:.1f} s, "
               f"largest at box {','.join(map(str, bb))} (source px)")
        crops = sorted(kept[tr["id"]], key=lambda c: c[1])
        rows.append((cap, th, [(c, f"#{tr['id']} {t:.1f}s") for _, t, c in crops]))
    sheet(rows, work.sheet, f"no faces found in {n} frames ({every} s apart)")
    for tr in tracks:
        tr.pop("last", None)
    json.dump({"clip": os.path.abspath(clip), "size": info["size"], "duration": info["duration"],
               "width": info["w"], "height": info["h"], "every": every, "frames": n,
               "detector": {"model": MODEL, "score": SCORE, "max_side": MAX_SIDE},
               "made": datetime.datetime.now().isoformat(timespec="seconds"),
               "tracks": tracks}, open(work.tracks, "w"), indent=1)
    print(f"{rel(work.tracks)}: {len(tracks)} track{'s' * (len(tracks) != 1)} in {n} frames ({every} s apart)")
    print(f"{rel(work.sheet)}: the sheet — pick tracks by the consent list, then: blur apply {os.path.basename(clip)} --tracks …")
    return 0


# ---- apply -----------------------------------------------------------------------------------

def parse_box(s):
    """x,y,w,h[@t1-t2] → (box, t1, t2); no @ = the whole clip."""
    try:
        xywh, _, span = s.partition("@")
        box = [int(float(v)) for v in xywh.split(",")]
        if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
            raise ValueError
        if span:
            a, b = span.split("-")
            t1, t2 = float(a), float(b)
            if t2 <= t1:
                raise ValueError
        else:
            t1, t2 = 0.0, float("inf")
        return box, t1, t2
    except ValueError:
        raise SystemExit(f"blur: --box {s!r}: want x,y,w,h@t1-t2 (pixels, seconds), e.g. 410,120,90,110@3.5-7")


def face(tr):
    """A track's identity across scans: its detections, whatever number it carries."""
    return tuple((d["t"], tuple(d["box"])) for d in tr["detections"])


def likely(old, tj):
    """The current track that is probably `old` after a re-scan that found it differently (another
    --every, another detection size): mean IoU above 0.5 at the old detections' times, measured on
    at least half of them. None when nothing matches, or there is no current scan."""
    best, best_id = 0.5, None
    for tr in (tj or {}).get("tracks", []):
        ious = [iou(d["box"], b) for d in old["detections"]
                if (b := at(tr["detections"], d["t"], tj["every"])) is not None]
        if len(ious) * 2 >= len(old["detections"]) and sum(ious) / len(ious) > best:
            best, best_id = sum(ious) / len(ious), tr["id"]
    return best_id


def box_key(s):
    """A manual box by what it covers, so 10,10,20,20@1-3 and 10,10,20,20@1.0-3.0 are one box."""
    box, t1, t2 = parse_box(s)
    return tuple(box), t1, t2


def at(dets, t, every):
    """A track's box at time t: interpolated between detections, held for one sample interval
    before the first and after the last (the face is there before the sampler sees it)."""
    if t < dets[0]["t"] - every or t > dets[-1]["t"] + every:
        return None
    if t <= dets[0]["t"]:
        return dets[0]["box"]
    for a, b in zip(dets, dets[1:]):
        if a["t"] <= t <= b["t"]:
            u = (t - a["t"]) / (b["t"] - a["t"]) if b["t"] > a["t"] else 0
            return [a["box"][i] + u * (b["box"][i] - a["box"][i]) for i in range(4)]
    return dets[-1]["box"]


def padded(box, pad, W, H):
    x, y, w, h = box
    x0, y0 = int(x - pad * w), int(y - pad * h)
    x1, y1 = int(x + w + pad * w + 0.999), int(y + h + pad * h + 0.999)
    return max(0, x0), max(0, y0), min(W, x1), min(H, y1)


def blur_region(frame, region, strength):
    cv2, _ = cv()
    x0, y0, x1, y1 = region
    if x1 <= x0 or y1 <= y0:
        return
    roi = frame[y0:y1, x0:x1]
    sigma = max(x1 - x0, y1 - y0) / strength
    frame[y0:y1, x0:x1] = cv2.GaussianBlur(roi, (0, 0), sigmaX=sigma, sigmaY=sigma)


def apply(clip, work, track_ids, boxes, pad, strength, who, reason, consent, drop=False):
    info = probe(clip)
    if info["hdr"]:
        print("blur: HDR source — the output is SDR without tone mapping; check its colours", file=sys.stderr)
    chosen, every = [], None
    tj = json.load(open(work.tracks)) if os.path.isfile(work.tracks) else None
    if tj and tj["size"] != info["size"]:
        if track_ids:
            raise SystemExit(f"blur: {work.tracks} was made from a different file ({tj['size']} bytes, this one "
                             f"{info['size']}) — scan again")
        tj = None
    if track_ids:
        if not tj:
            raise SystemExit(f"blur: no {work.tracks} — run: blur scan {clip}")
        every = tj["every"]
        byid = {tr["id"]: tr for tr in tj["tracks"]}
        missing = [i for i in track_ids if i not in byid]
        if missing:
            raise SystemExit(f"blur: no track {missing} in {work.tracks} (it has 1-{len(byid)})")
        chosen = [byid[i] for i in track_ids]
    manual = [parse_box(b) for b in boxes]
    if not chosen and not manual:
        raise SystemExit("blur: nothing to blur — give --tracks from the sheet, or --box")
    W, H, fps = info["w"], info["h"], info["fps"]
    for b, (box, _, _) in zip(boxes, manual):
        x0, y0, x1, y1 = padded(box, 0, W, H)
        if x1 <= x0 or y1 <= y0:
            raise SystemExit(f"blur: --box {b} lies outside the picture ({W}x{H}, upright, source pixels)")
    # apply starts from the source every time, so an apply that leaves out what the last one
    # blurred would quietly bring those faces back — and check judges only the latest apply.
    # A face is known by its detections, not its number: a re-scan renumbers every track, and
    # an identical re-scan gives the same detections under the same or other numbers.
    work.owned(work.applied, clip, info["size"])
    prev = json.load(open(work.applied)) if os.path.isfile(work.applied) else {}
    prev_faces = {face(tr): tr for tr in prev.get("chosen", [])}
    prev_boxes = {box_key(b) for b in prev.get("boxes", [])}
    now_faces = {face(tr) for tr in chosen}
    now_boxes = {box_key(b) for b in boxes}
    renumbered, lost = [], []   # (old track, its number in the current scan or None)
    for f, tr in prev_faces.items():
        if f in now_faces:
            continue
        same = [t["id"] for t in (tj or {}).get("tracks", []) if face(t) == f]
        if same:
            renumbered.append((tr, same[0]))
        else:
            lost.append((tr, likely(tr, tj)))
    gone_b = [b for b in prev.get("boxes", []) if box_key(b) not in now_boxes]

    def said(tr, now):
        d = tr["detections"][0]
        where = f"from {d['t']} s at {','.join(map(str, d['box']))}"
        if now is None:
            return f"was #{tr['id']} ({where}; not found in the current scan)"
        sure = any(tr is r for r, _ in renumbered)
        return f"was #{tr['id']} ({where}; now {'#' if sure else 'probably #'}{now})"

    if (renumbered or lost or gone_b) and not drop:
        lines = []
        if renumbered:
            lines.append("the same faces under new numbers: " + "; ".join(said(t, n) for t, n in renumbered)
                         + " — include those numbers")
        if lost:
            lines.append(("faces this scan found differently" if tj else "faces from a scan that is gone — scan again")
                         + ": " + "; ".join(said(t, n) for t, n in lost)
                         + " — find each on the sheet, then apply with every pick and --drop, without --who")
        if gone_b:
            lines.append(f"boxes {gone_b} — repeat them")
        raise SystemExit("blur: the last apply also blurred what this one leaves out (apply starts from the "
                         "source; --drop leaves them out on purpose):\n  " + "\n  ".join(lines))
    if drop and (renumbered or lost or gone_b):
        picked = set(track_ids)
        left = [said(t, n) for t, n in renumbered + lost if n not in picked]
        moved = [said(t, n) for t, n in renumbered + lost if n in picked]
        if moved:
            print("blur: --drop: re-picked under new numbers: " + "; ".join(moved), file=sys.stderr)
        if left or gone_b:
            print("blur: --drop: UN-BLURRED on purpose: " + "; ".join(left + [f"box {b}" for b in gone_b]),
                  file=sys.stderr)
    # Each apply repeats every earlier track and box, so the log takes only what this one adds:
    # the person named by --who is those tracks and boxes, not the whole list.
    new_t = [tr for tr in chosen if face(tr) not in prev_faces]
    new_b = [b for b in boxes if box_key(b) not in prev_boxes]
    if who or reason or consent:
        try:
            import yaml  # noqa: F401  (before the encode: a failed log must not leave a logged-looking apply)
        except ImportError:
            raise SystemExit("blur: --who needs PyYAML — pip3 install --user pyyaml")
        if lost:
            # a re-picked face found differently would look new and be logged as this person's
            raise SystemExit("blur: --who/--reason/--consent cannot tell the re-picked faces from the new ones "
                             "after this scan — apply with every pick and --drop first, without them; then add "
                             "the new person on the next apply")
        if not new_t and not new_b:
            raise SystemExit("blur: --who/--reason/--consent log nobody new — this apply adds no track or box the "
                             "last one lacked; give them on the apply that first blurs that person")
    ext = ".mov" if any(c.startswith("pcm_") for c in info["audio"]) else ".mp4"
    out = os.path.join(os.path.dirname(os.path.abspath(clip)), work.stem + "-blur" + ext)
    tmp = out + ".part" + ext
    # The source's metadata stays behind (a camera's GPS `location` tag would locate the people
    # this hides); only its creation time comes across. setparams, not -color_* output flags,
    # is what makes the BT.709 tags reach the file (probed: the flags alone left them unknown).
    created = ["-metadata", f"creation_time={info['created']}"] if info["created"] else []
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
         "-framerate", info["rate"], "-i", "-", "-i", clip, "-map", "0:v", "-map", "1:a?",
         "-map_metadata", "-1", *created,
         "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
         "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
         "-c:a", "copy", "-movflags", "+faststart", tmp], stdin=subprocess.PIPE)
    n = touched = 0
    broken = False
    try:
        for i, f in enumerate(frames(clip, W, H, f"fps={info['rate']}")):
            f = f.copy()
            t = i / fps
            hit = False
            for tr in chosen:
                b = at(tr["detections"], t, every)
                if b is not None:
                    blur_region(f, padded(b, pad, W, H), strength)
                    hit = True
            for box, t1, t2 in manual:
                if t1 <= t <= t2:
                    blur_region(f, padded(box, 0, W, H), strength)
                    hit = True
            touched += hit
            n += 1
            try:
                enc.stdin.write(f.tobytes())
            except BrokenPipeError:   # the encoder died: an audio codec the container refuses, a full disk
                broken = True
                break
    finally:
        try:
            enc.stdin.close()
        except BrokenPipeError:
            broken = True
        rc = enc.wait()
    if rc or broken:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise SystemExit(f"blur: ffmpeg failed writing {out} (exit {rc}) — its message is above; nothing was written")
    # The log first: a record written before a failed log would make the retry's --who "nobody new";
    # the output replaces the last one only after both, so it never runs ahead of its record.
    if who or reason or consent:
        log(work, clip, out, new_t, new_b, who, reason, consent)
    # check reads this record alone: a later re-scan renumbers tracks.json, so the chosen tracks
    # are kept here as they were blurred, with the grid they were found on and the source's size.
    rec = {"source": os.path.abspath(clip), "size": info["size"], "output": out, "tracks": track_ids,
           "every": every, "chosen": chosen, "boxes": boxes,
           "pad": pad, "strength": strength, "frames": n, "blurred_frames": touched,
           "made": datetime.datetime.now().isoformat(timespec="seconds")}
    json.dump(rec, open(work.applied, "w"), indent=1)
    os.replace(tmp, out)
    print(f"{rel(out)}: {touched} of {n} frames blurred (tracks {track_ids or '-'}, boxes {len(manual)}); audio copied")
    print(f"next: blur check {os.path.basename(clip)}")
    return 0


def log(work, clip, out, tracks, boxes, who, reason, consent):
    """Append one entry to $WORK/blur/log.yaml — names live here and nowhere else. Each track is
    the one this apply added, with its number now and where it first appears: the number can
    change with a re-scan, the face's first detection does not."""
    import yaml
    faces = [{"track": tr["id"], "from": tr["detections"][0]["t"], "box": tr["detections"][0]["box"]}
             for tr in tracks]
    entry = {"date": datetime.date.today().isoformat(), "clip": os.path.basename(clip),
             "output": os.path.basename(out), "tracks": faces, "boxes": boxes,
             "who": who or "", "reason": reason or "", "consent": consent or ""}
    with open(work.log, "a") as fh:
        yaml.safe_dump([entry], fh, sort_keys=False, allow_unicode=True)
    print(f"{rel(work.log)}: logged (outside git; never copy it into an EDL or a commit)")


# ---- check -----------------------------------------------------------------------------------

def detail(frame, region):
    """Laplacian variance inside the region: how much fine detail (edges, eyes, letters) is left."""
    cv2, _ = cv()
    x0, y0, x1, y1 = region
    roi = frame[y0:y1, x0:x1]
    if roi.size == 0:
        return 0.0
    return float(cv2.Laplacian(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def check(clip, work):
    if not os.path.isfile(work.applied):
        raise SystemExit(f"blur: no {work.applied} — run: blur apply {clip} …")
    rec = json.load(open(work.applied))
    info = probe(clip)
    if rec.get("size") != info["size"]:
        raise SystemExit(f"blur: {work.applied} records an apply on {rec['source']}, not this file — "
                         f"apply this clip again (or give it its own --work)")
    out = rec["output"]
    if not os.path.isfile(out):
        raise SystemExit(f"blur: {out} is gone — apply again")
    W, H, fps = info["w"], info["h"], info["fps"]
    chosen, every = rec["chosen"], rec["every"]
    # Twice as dense as the scan: half the samples fall between two detections, where the box is
    # interpolated and a fast-moving face is most likely to slip out of it.
    grid = every / 2 if chosen else 0.1
    want = {}   # sample index on the grid -> [(kind, id, time, box)]
    for tr in chosen:
        ds = tr["detections"]
        k0 = max(0, int(-(-(ds[0]["t"] - every) // grid)))
        for k in range(k0, int((ds[-1]["t"] + every) // grid) + 1):
            b = at(ds, k * grid, every)
            if b is not None:
                want.setdefault(k, []).append(("track", tr["id"], round(k * grid, 3), [round(v) for v in b]))
    manual = [parse_box(b) for b in rec["boxes"]]
    edge = 1.5 / fps   # a sample is the frame at its time or the one before: keep clear of the window's ends
    for j, (box, t1, t2) in enumerate(manual, 1):
        lo, hi = t1 + edge, min(t2, info["duration"]) - edge
        ks = list(range(max(0, int(-(-lo // grid))), int(hi // grid) + 1))
        end = min(t2, info["duration"])
        if not ks and t1 <= end:   # a window shorter than the grid and its margins: its middle frame
            ks = [round((t1 + end) / 2 / grid)]
        for k in ks:
            want.setdefault(k, []).append(("box", j, round(k * grid, 3), box))
    # the source's detail per box, measured on the fly: holding 4K frames would cost GBs
    before = {}
    for k, (_, f) in enumerate(samples(clip, info, grid)):
        for kind, i, t, b in want.get(k, []):
            before[(k, kind, i)] = detail(f, padded(b, 0, W, H))
    det = Detector(W, H)
    res, crops = {}, {}
    for k, (_, f) in enumerate(samples(out, info, grid)):
        if k not in want:
            continue
        faces = det(f)
        for kind, i, t, b in want[k]:
            # a face overlapping the box, or beside it (it moved off the interpolated path), counts
            found = kind == "track" and any(iou(fb, b) > 0.1 or near(fb, b, 0.75) for fb, _ in faces)
            b0 = before.get((k, kind, i), 0.0)
            ratio = detail(f, padded(b, 0, W, H)) / b0 if b0 else 0.0
            res.setdefault((kind, i), []).append((t, found, ratio))
            cs = crops.setdefault((kind, i), [])
            cs.append((b[2] * b[3], t, crop(f, b)))
            cs.sort(key=lambda c: -c[0])
            del cs[CROPS:]
    bad = 0
    rows = []
    for (kind, i), rs in sorted(res.items()):
        n = len(rs)
        # still readable: the detector finds the face, or the box kept over SHARP of its detail
        found = [t for t, fd, r in rs if fd or r > SHARP]
        worst = max(r for _, _, r in rs)
        name = f"track {i}" if kind == "track" else f"box {i} ({rec['boxes'][i - 1]})"
        if found:
            bad += 1
            ts = ", ".join(f"{t:.2f}" for t in found[:6]) + (" …" if len(found) > 6 else "")
            print(f"{name}, {n} frame{'s' * (n != 1)}: still readable at {len(found)} (t = {ts} s; detail up to "
                  f"{worst:.0%} of the source) — apply again with a larger --pad, a lower --strength, or a --box there")
        else:
            what = "no face found; detail" if kind == "track" else "detail"
            print(f"{name}, {n} frame{'s' * (n != 1)}, unreadable at every one ({what} at most {worst:.0%} of the source)")
        rows.append((f"{name}: {'STILL READABLE' if found else 'unreadable'}", None,
                     [(c, f"{t:.2f}s") for _, t, c in sorted(crops[(kind, i)], key=lambda c: c[1])]))
    for tr in chosen:
        if ("track", tr["id"]) not in res:
            bad += 1
            print(f"track {tr['id']}: no frame of the blurred clip was measured — check the clip's length")
    for j, (box, t1, t2) in enumerate(manual, 1):
        if ("box", j) not in res:
            bad += 1
            print(f"box {j} ({rec['boxes'][j - 1]}): no frame of the blurred clip was measured — its window "
                  f"lies outside the clip ({info['duration']:.1f} s)")
    sheet(rows, work.checksheet, "nothing was measured")
    print(f"{rel(work.checksheet)}: the result, between and at the scan's frames")
    return 1 if bad else 0


# ---- entry -----------------------------------------------------------------------------------

def ids(s):
    try:
        return sorted({int(x) for x in s.split(",") if x.strip()})
    except ValueError:
        raise SystemExit(f"blur: --tracks {s!r}: want numbers from the sheet, e.g. 2,5")


def main(argv):
    ap = cli.parser("blur", __doc__)
    ap.add_argument("verb", choices=["scan", "apply", "check"])
    ap.add_argument("clip", help="the source clip (relative paths resolve against --media)")
    ap.add_argument("--work", help="the work folder outside git (default $WORK); writes <work>/blur/")
    ap.add_argument("--every", type=float, default=0.5, help="scan: seconds between sampled frames (default 0.5)")
    ap.add_argument("--tracks", default="", help="apply: track numbers from the sheet, e.g. 2,5")
    ap.add_argument("--box", action="append", default=[], metavar="x,y,w,h@t1-t2",
                    help="apply: a manual box, pixels and seconds; repeat for more")
    ap.add_argument("--pad", type=float, default=0.4, help="apply: track boxes grow by this share of their size on each side (default 0.4)")
    ap.add_argument("--strength", type=float, default=5.0,
                    help="apply: Gaussian sigma = box size / this (default 5; lower is stronger)")
    ap.add_argument("--who", help="apply: who the blurred person is — written to $WORK/blur/log.yaml only")
    ap.add_argument("--reason", help="apply: why (log only)")
    ap.add_argument("--consent", help="apply: consent state, e.g. refused, not asked (log only)")
    ap.add_argument("--drop", action="store_true",
                    help="apply: leave out tracks or boxes the last apply blurred, un-blurring them on purpose")
    a = ap.parse_args(argv)
    r = cli.roots(a)
    clip = r.m(a.clip)
    if not os.path.isfile(clip):
        raise SystemExit(f"blur: no clip at {clip}")
    work = Work(a.work or os.environ.get("WORK"), clip)
    if a.verb == "scan":
        if a.every <= 0:
            raise SystemExit("blur: --every must be above 0")
        return scan(clip, work, a.every)
    if a.verb == "apply":
        return apply(clip, work, ids(a.tracks), a.box, a.pad, a.strength, a.who, a.reason, a.consent, a.drop)
    return check(clip, work)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
