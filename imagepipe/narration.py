"""
Local narration audio — Section 4.3 of NEXT_PHASE_PLAN.md. Kokoro-82M,
Apache 2.0, fully commercial, CPU-friendly (~2-3GB or CPU-only) — chosen
specifically because it renders exactly the text it's given, so unlike the
image-generation/hallucination-check path, there's nothing to fact-check
here: no vision-model verification step needed for narration audio.

Deliberately CPU-only by default (device="cpu" hardcoded below, not
detected) — this machine's 4GB GPU is already contended by other running
processes (Ollama, another project's model), and Kokoro-82M is small enough
that CPU inference is practical for narration-length text, unlike SD1.5
image generation which genuinely needs the GPU.

Requires `kokoro-onnx` (installed). Model + voice files aren't pulled via
huggingface_hub's from_pretrained; kokoro-onnx expects the .onnx model and
voices file downloaded separately from its GitHub releases into
imagepipe/.kokoro_models/ — v1.0 file names below (v0_19 is the older,
now-superseded release naming).
"""
import os

_MODEL_DIR = os.path.join(os.path.dirname(__file__), ".kokoro_models")
_MODEL_PATH = os.path.join(_MODEL_DIR, "kokoro-v1.0.onnx")
_VOICES_PATH = os.path.join(_MODEL_DIR, "voices-v1.0.bin")

DEFAULT_VOICE = "af_sarah"


def models_available() -> bool:
    return os.path.exists(_MODEL_PATH) and os.path.exists(_VOICES_PATH)


def load_narrator():
    from kokoro_onnx import Kokoro
    if not models_available():
        raise RuntimeError(
            f"Kokoro model files not found in {_MODEL_DIR} — download "
            f"kokoro-v1.0.onnx and voices-v1.0.bin from the kokoro-onnx "
            f"GitHub releases first."
        )
    return Kokoro(_MODEL_PATH, _VOICES_PATH)


def narrate(kokoro, text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0, lang: str = "en-us"):
    """Returns (samples: np.ndarray, sample_rate: int) — write with
    soundfile.write(path, samples, sample_rate) to get a .wav."""
    samples, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=lang)
    return samples, sample_rate


def narrate_to_file(kokoro, text: str, out_path: str, voice: str = DEFAULT_VOICE, speed: float = 1.0):
    import soundfile as sf
    samples, sample_rate = narrate(kokoro, text, voice=voice, speed=speed)
    sf.write(out_path, samples, sample_rate)
    return out_path
