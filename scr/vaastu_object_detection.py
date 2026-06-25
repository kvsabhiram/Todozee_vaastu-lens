
"""
Room classifier from a single photo.

Detects objects with YOLOv8 (COCO weights), then infers the room type from
which objects are present. Supported rooms (COCO-detectable only):
    master_bedroom, living_room, kitchen, bathroom, dining_room.

Usage:
    python vaastu_object_detection.py path/to/photo.jpg
    python vaastu_object_detection.py path/to/folder/
    python vaastu_object_detection.py photo.jpg --save out.jpg --json result.json
"""

import argparse
import json
from collections import Counter
from pathlib import Path

import cv2
import torch
from ultralytics import YOLO

from datetime import datetime

import scr.vastu_rules as vastu_rules

HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent  # project root; model + data folders live one level above scr/
MODEL_PATH = ROOT / "yolov8m.pt"
CONF_TH = 0.40
IOU_TH = 0.45

# Internal logs: every classification saves its annotated photo here plus a
# JSON line of the full reasoning (room, confidence, matched cues, scores,
# objects, detections). Not part of the API output — purely for inspection.
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "classifications.jsonl"

# Detection weights and badge colors come straight from the Vastu database
# (English file), so the classifier and the rules can never drift apart.
#   ROOM_RULES        : {slug: {yolo_class: weight}}
#   ROOM_BADGE_COLOR  : {slug: (B, G, R)}
# Some rooms (office, study_room, corridor, main_entrance) include non-COCO
# indicator classes; with the default yolov8m.pt (COCO) those contribute 0 and
# the COCO indicators alone drive the classification.
#
# For reference, each room is detected based on these indicator objects
# (class: weight) — higher weight = stronger / more decisive signal:
#   master_bedroom : bed 5.0, clock 0.5
#   living_room    : couch 4.0, tv 2.0, remote 1.5, potted plant 0.8, vase 0.6, book 0.4
#   kitchen        : refrigerator 4.0, microwave 3.0, oven 3.0, toaster 2.0, sink 1.5,
#                    bowl 0.6, knife 0.6, bottle 0.4, cup 0.3
#   bathroom       : toilet 5.0, hair drier 2.0, toothbrush 2.0, sink 1.5
#   dining_room    : dining table 4.0, wine glass 1.5, fork 1.0, spoon 0.8, knife 0.6,
#                    chair 0.3, cup 0.3
#   office         : desk 4.0*, laptop 4.0, computer 3.0*, monitor 3.0*, printer 2.0*,
#                    whiteboard 2.0*, filing_cabinet 1.5*, tv/mouse/keyboard 1.0,
#                    cell phone 0.5, chair 0.3, book 0.2
#   study_room     : bookshelf 4.0*, book 3.0, desk 3.0*, lamp 2.0*, notebook 1.5*,
#                    globe 1.0*, laptop 1.0, pen 0.5*, chair/clock 0.3
#   corridor       : door 3.0*, wall_lamp 2.0*, picture 1.5*
#   main_entrance  : shoe_rack 4.0*, entrance_mat 4.0*, door 3.0*
# (* = non-COCO class; only fires with a custom vastu-trained model.)
ROOM_RULES = vastu_rules.detection_rules()
ROOM_BADGE_COLOR = vastu_rules.badge_colors()


def infer_room(detected_classes):
    """Score each room by weighted sum of its indicator classes.

    Returns (room, confidence, scores, matched) where `matched` is the winning
    room's detected indicator classes mapped to the weighted contribution they
    each added — i.e. *why* this room was chosen.
    """
    if not detected_classes:
        return "unknown", 0.0, {}, {}

    counts = Counter(detected_classes)
    scores = {}
    contributions = {}  # room -> {class: weighted contribution}
    for room, indicators in ROOM_RULES.items():
        s = 0.0
        contrib = {}
        for cls, w in indicators.items():
            if cls in counts:
                c = w * counts[cls]
                s += c
                contrib[cls] = round(c, 2)
        if s > 0:
            scores[room] = s
            contributions[room] = contrib

    if not scores:
        return "unknown", 0.0, {}, {}

    best_room = max(scores, key=scores.get)
    confidence = scores[best_room] / sum(scores.values())
    matched = dict(sorted(contributions[best_room].items(), key=lambda kv: -kv[1]))
    return best_room, round(confidence, 2), scores, matched


def draw_box(frame, label, conf, x1, y1, x2, y2, color=(120, 200, 120)):
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    txt = f"{label} {conf:.0%}"
    (tw, th), bl = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.rectangle(frame, (x1, y1 - th - bl - 4), (x1 + tw + 4, y1), color, -1)
    cv2.putText(frame, txt, (x1 + 2, y1 - bl - 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (10, 10, 10), 1, cv2.LINE_AA)


def draw_badge(frame, room, conf, obj_counts):
    h, w = frame.shape[:2]
    bw, bh = 320, 80
    x0, y0 = w - bw - 10, 10
    color = ROOM_BADGE_COLOR.get(room, ROOM_BADGE_COLOR["unknown"])
    overlay = frame.copy()
    cv2.rectangle(overlay, (x0, y0), (x0 + bw, y0 + bh), color, -1)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
    cv2.rectangle(frame, (x0, y0), (x0 + bw, y0 + bh), color, 2)
    cv2.putText(frame, room.replace("_", " ").upper(), (x0 + 8, y0 + 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(frame, f"{conf:.0%}", (x0 + bw - 60, y0 + 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 255, 220), 1, cv2.LINE_AA)
    items = [f"{v}x{k}" for k, v in list(obj_counts.items())[:4]]
    cv2.putText(frame, "  ".join(items), (x0 + 8, y0 + 56),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (235, 235, 235), 1, cv2.LINE_AA)


def _log_classification(image_path, frame, reasoning: dict) -> None:
    """Persist the annotated photo + full reasoning to the internal logs.

    Saves the annotated frame as logs/<timestamp>_<room>.jpg and appends one
    JSON line to logs/classifications.jsonl that links to that photo and holds
    all the internal reasoning. Best-effort: logging never breaks a request.
    """
    try:
        LOG_DIR.mkdir(exist_ok=True)
        ts = datetime.now()
        stamp = ts.strftime("%Y%m%d_%H%M%S_%f")
        room = reasoning.get("room", "unknown")
        photo_name = f"{stamp}_{room}.jpg"
        cv2.imwrite(str(LOG_DIR / photo_name), frame)

        entry = {
            "timestamp": ts.isoformat(),
            "source_image": Path(image_path).name,
            "log_photo": photo_name,
            **reasoning,
        }
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        print(f"[{Path(image_path).name}] {reasoning.get('reason', '')} "
              f"({reasoning.get('room_confidence', 0):.0%})  ->  logs/{photo_name}")
    except Exception as e:  # never let logging crash the classification
        print(f"⚠️  logging failed: {e}")


def classify_image(model, image_path, conf=CONF_TH, save_path=None, language="English"):
    """Detect objects in one photo, infer the room, and attach localized rules.

    `language` is a language name like 'Telugu' or 'Hindi' (an ISO code also
    works). When no room is detected, the room is "unknown" and the localized
    "unknown" block is returned as the rules.
    """
    frame = cv2.imread(str(image_path))
    if frame is None:
        raise FileNotFoundError(f"Cannot read image: {image_path}")

    results = model(frame, conf=conf, iou=IOU_TH, verbose=False)

    detections = []
    detected_classes = []
    for r in results:
        for box in (r.boxes or []):
            cls_id = int(box.cls[0])
            cls_name = r.names[cls_id]
            cf = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            detections.append({
                "class": cls_name,
                "conf":  round(cf, 3),
                "bbox":  [x1, y1, x2, y2],
            })
            detected_classes.append(cls_name)
            draw_box(frame, cls_name, cf, x1, y1, x2, y2)

    # ── Internal processing (logged, but NOT returned) ───────────────────
    # room scoring, the matched cues, raw object counts and the per-box
    # detections all stay internal — used for the badge overlay and saved to
    # the internal logs as the reasoning, but kept out of the output payload.
    room, room_conf, scores, matched = infer_room(detected_classes)
    obj_counts = dict(Counter(detected_classes).most_common())
    draw_badge(frame, room, room_conf, obj_counts)

    if room == "unknown":
        reason = "No recognized room-defining objects were detected."
    else:
        reason = f"Detected {', '.join(matched.keys())} -> classified as {room}."

    _log_classification(
        image_path, frame,
        reasoning={
            "room":            room,
            "room_confidence": room_conf,
            "reason":          reason,
            "matched_objects": matched,
            "scores":          {k: round(v, 2) for k, v in scores.items()},
            "objects":         obj_counts,
            "detections":      detections,
        },
    )

    # ── Output: only what was detected + the rules ───────────────────────
    result = {
        "room":  room,
        "rules": vastu_rules.get_rules(room, language),
    }

    if save_path:
        cv2.imwrite(str(save_path), frame)
        result["annotated"] = str(save_path)

    return result


def load_model(path=None):
    p = Path(path or MODEL_PATH)
    if not p.exists():
        raise FileNotFoundError(f"YOLO weights not found: {p}")
    dev = "cuda:0" if torch.cuda.is_available() else "cpu"
    model = YOLO(str(p))
    model.to(dev)
    print(f"Loaded {p.name} on {dev}")
    return model


def classify_folder(model, folder, out_dir, conf=CONF_TH, language="English"):
    out_dir.mkdir(exist_ok=True)
    images = sorted([*folder.glob("*.jpg"), *folder.glob("*.jpeg"), *folder.glob("*.png")])
    summary = []
    for img in images:
        res = classify_image(model, img, conf=conf, save_path=out_dir / f"cls_{img.name}",
                             language=language)
        summary.append(res)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nSummary written to {out_dir / 'summary.json'}")
    return summary


def main():
    ap = argparse.ArgumentParser(description="Classify the room type in a photo.")
    ap.add_argument("source", help="Path to a single image OR a folder of images.")
    ap.add_argument("--model", default=str(MODEL_PATH), help="YOLO weights path.")
    ap.add_argument("--conf", type=float, default=CONF_TH, help="Detection confidence threshold.")
    ap.add_argument("--lang", default="English",
                    help="Rules language name: English, Telugu, Hindi, Tamil, ... Default: English.")
    ap.add_argument("--save", default=None, help="Annotated output path (single image) or folder.")
    ap.add_argument("--json", default=None, help="Write JSON result to this path.")
    args = ap.parse_args()

    model = load_model(args.model)
    src = Path(args.source)

    if src.is_dir():
        out_dir = Path(args.save) if args.save else src.parent / "classified"
        result = classify_folder(model, src, out_dir, conf=args.conf, language=args.lang)
    else:
        res = classify_image(model, src, conf=args.conf, save_path=args.save, language=args.lang)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        result = res

    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()