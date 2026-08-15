"""
Local image-to-text captioning — stands in for the plan's Gemini vision
check (4.3) since no GEMINI_API_KEY is configured yet (see SESSION_ADVICE.txt).
BLIP is small (~1GB), fully local, no API key, good enough to sanity-check
whether a generated image still reads as the intended description. Swap for
Gemini later without touching the rest of the harness — describe_image()'s
signature (PIL.Image -> str) is the only contract that matters.
"""
import os

_HF_CACHE = os.path.join(os.path.dirname(__file__), ".hf_cache")
os.environ.setdefault("HF_HOME", _HF_CACHE)
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", _HF_CACHE)

MODEL_ID = "Salesforce/blip-image-captioning-base"


def load_captioner():
    import torch
    from transformers import BlipProcessor, BlipForConditionalGeneration

    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = BlipProcessor.from_pretrained(MODEL_ID)
    model = BlipForConditionalGeneration.from_pretrained(MODEL_ID).to(device)
    return processor, model


def describe_image(processor, model, image) -> str:
    import torch
    device = next(model.parameters()).device
    inputs = processor(image, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=40)
    return processor.decode(out[0], skip_special_tokens=True)


def unload_captioner(processor, model):
    """.to("cpu") frees GPU VRAM immediately regardless of lingering
    references elsewhere — see sd_pipeline.unload_pipeline for why plain
    del isn't enough."""
    import gc
    import torch
    if torch.cuda.is_available():
        model.to("cpu")
    del processor, model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
