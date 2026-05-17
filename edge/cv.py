"""
Aegis — Edge Computer Vision & TTS
==================================
YOLOv8-nano and HOG pedestrian detection logic.
"""
import logging
from pathlib import Path
from collections import Counter

from rich.console import Console

log = logging.getLogger("aegis.edge.cv")
console = Console()

# YOLO COCO class IDs we care about in disaster scenarios
YOLO_HAZARD_CLASSES = {
    0: ("person", (0, 255, 0)),      # green
    2: ("vehicle", (255, 165, 0)),   # orange
    7: ("truck", (255, 165, 0)),     # orange
    15: ("cat", (200, 200, 0)),      # animal
    16: ("dog", (200, 200, 0)),      # animal
    67: ("cell phone", (100, 100, 255)),
}

# Custom label overrides for disaster context
DISASTER_LABELS = {
    0: "Civilian",
    2: "Stranded Vehicle",
    7: "Emergency Vehicle",
    15: "Animal",
    16: "Animal",
}

_yolo_model = None  # lazy-loaded singleton


def get_yolo_model():
    """Lazy-load the YOLOv8-nano model."""
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO
        model_path = Path("models") / "yolov8n.pt"
        if not model_path.exists():
            console.print("[bold yellow]⬇️ Downloading YOLOv8-nano weights...[/]")
        _yolo_model = YOLO(str(model_path))
        console.print("[bold green]✅ YOLOv8-nano model loaded.[/]")
    return _yolo_model


def run_edge_detection(frame):
    """
    Run edge CV detection on a frame.
    Tries YOLOv8-nano first, falls back to HOG.
    """
    try:
        return _run_yolo_detection(frame)
    except (ImportError, Exception) as e:
        log.warning("YOLOv8 unavailable (%s), falling back to HOG.", e)
        return _run_hog_detection(frame)


def _run_yolo_detection(frame):
    """YOLOv8-nano multi-class detection."""
    import cv2
    console.print("[bold cyan]🤖 Running YOLOv8-nano edge detection...[/]")
    model = get_yolo_model()
    results = model(frame, conf=0.35, verbose=False)

    detections = []
    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])

            if cls_id in YOLO_HAZARD_CLASSES:
                label_base, color = YOLO_HAZARD_CLASSES[cls_id]
                label = DISASTER_LABELS.get(cls_id, label_base)
                x1, y1, x2, y2 = map(int, box.xyxy[0])

                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    frame,
                    f"{label} {conf:.0%}",
                    (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2,
                )
                detections.append({
                    "class": label, "confidence": round(conf, 2),
                    "bbox": [x1, y1, x2, y2],
                })

    counts = Counter(d["class"] for d in detections)
    if detections:
        summary_parts = [f"{v} {k}(s)" for k, v in counts.items()]
        console.print(f"[bold red]⚠️ Detected: {', '.join(summary_parts)}[/]")
    else:
        console.print("[bold green]✅ No hazard objects detected.[/]")

    return frame, detections


def _run_hog_detection(frame):
    """Legacy OpenCV HOG pedestrian detector (fallback)."""
    import cv2
    console.print("[bold cyan]🤖 Running legacy HOG pedestrian detection...[/]")
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    boxes, weights = hog.detectMultiScale(frame, winStride=(8, 8))
    detections = []

    for (x, y, w, h) in boxes:
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(frame, 'Civilian', (x, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        detections.append({"class": "Civilian", "confidence": 0.7,
                           "bbox": [x, y, x + w, y + h]})

    if len(boxes) > 0:
        console.print(f"[bold red]⚠️ Detected {len(boxes)} civilian(s) at edge![/]")
    else:
        console.print("[bold green]✅ Area clear of civilians.[/]")

    return frame, detections


def play_tts(text: str) -> None:
    """Read out the dispatch plan using text-to-speech."""
    try:
        import pyttsx3
        import re
        console.print("[bold cyan]🔊 Playing Dispatch Plan TTS...[/]")
        engine = pyttsx3.init()
        engine.setProperty('rate', 170)
        
        # Clean up markdown for TTS
        clean_text = re.sub(r'#|\*|_|🚨|✅|⚠️|🚫', '', text)
        clean_text = re.sub(r'\[.*?\]', '', clean_text)
        
        engine.say("Command Node acknowledges report. Here is the dispatch plan:")
        engine.say(clean_text)
        engine.runAndWait()
    except Exception as e:
        log.error("TTS engine failed: %s", e)
