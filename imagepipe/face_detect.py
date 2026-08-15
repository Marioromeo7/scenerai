"""
Real face/eye detection — replaces the fixed-height-band guess in
sd_inpaint.py's eye_color mask, which a direct visual audit showed missing
the actual eyes in 5 of 7 sampled failures (profile poses, closed eyes,
faces sitting lower/higher in frame than the 15-35% assumption). MediaPipe
Face Detection (Apache 2.0, CPU-only, a few hundred KB model) gives real
eye keypoints per image instead of a guessed rectangle.

Also used as a pre-inpaint sanity gate: one of the 7 audited failures had
no face in frame at all (a broader generation failure, not a targeting
problem) — no mask placement, however smart, fixes that. Detecting "no
face" lets the caller skip wasting an inpaint attempt on it entirely.

Uses the newer MediaPipe Tasks API (mp.tasks.vision) — the legacy
mp.solutions.face_detection API from older tutorials/docs no longer
exists in mediapipe 1.0.0, and this needs a model bundle downloaded
separately (not bundled with the pip package), fetched once into
.mediapipe_models/ by whoever set this up.
"""
import os

_MODEL_PATH = os.path.join(os.path.dirname(__file__), ".mediapipe_models", "blaze_face_short_range.tflite")

_face_detector = None


def _get_detector():
    """Lazy singleton — model load has real (if small) cost, don't repeat
    it per-image."""
    global _face_detector
    if _face_detector is None:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        if not os.path.exists(_MODEL_PATH):
            raise RuntimeError(
                f"MediaPipe face detector model not found at {_MODEL_PATH} -- "
                f"download blaze_face_short_range.tflite from "
                f"https://storage.googleapis.com/mediapipe-models/face_detector/"
                f"blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"
            )
        options = mp_vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=_MODEL_PATH),
            min_detection_confidence=0.5,
        )
        _face_detector = mp_vision.FaceDetector.create_from_options(options)
    return _face_detector


def detect_eyes(image):
    """Returns {"left_eye": (x, y), "right_eye": (x, y)} in pixel
    coordinates, or None if no face was detected. BlazeFace's keypoint
    order is [right_eye, left_eye, nose_tip, mouth_center, ...]."""
    import numpy as np
    import mediapipe as mp

    detector = _get_detector()
    rgb = np.array(image.convert("RGB"))
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = detector.detect(mp_image)
    if not result.detections:
        return None

    detection = result.detections[0]  # highest-confidence face
    kp = detection.keypoints
    w, h = image.size
    return {
        "right_eye": (kp[0].x * w, kp[0].y * h),
        "left_eye": (kp[1].x * w, kp[1].y * h),
    }


def eye_mask_box(image, margin_frac: float = 0.18):
    """Tight box around both detected eyes, padded by margin_frac of the
    face width on each side. Returns (left, top, right, bottom) in pixels,
    or None if no face was detected — caller decides the fallback."""
    eyes = detect_eyes(image)
    if eyes is None:
        return None
    w, h = image.size
    xs = [eyes["left_eye"][0], eyes["right_eye"][0]]
    ys = [eyes["left_eye"][1], eyes["right_eye"][1]]
    eye_span = max(abs(xs[0] - xs[1]), w * 0.15)  # floor, in case both eyes landed ~identical (near-profile)
    margin = eye_span * margin_frac
    left = max(0, min(xs) - eye_span * 0.6 - margin)
    right = min(w, max(xs) + eye_span * 0.6 + margin)
    top = max(0, min(ys) - eye_span * 0.5 - margin)
    bottom = min(h, max(ys) + eye_span * 0.5 + margin)
    return (int(left), int(top), int(right), int(bottom))
