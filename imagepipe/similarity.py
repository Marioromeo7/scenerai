"""
CLIP-based consistency scoring — the "optional, more numeric" measure from
plan 4.2, promoted to primary here since it's fully local (no API key,
unlike the Gemini description-diff path). One model does both jobs:
  - image-image cosine similarity: does generation N look like the
    canonical reference?
  - text-text cosine similarity: does BLIP's caption of generation N still
    match the original structured-profile text? A cheap proxy for the
    Gemini "diff the description against intended fields" step.
"""
import os

_HF_CACHE = os.path.join(os.path.dirname(__file__), ".hf_cache")
os.environ.setdefault("HF_HOME", _HF_CACHE)
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", _HF_CACHE)

MODEL_ID = "openai/clip-vit-base-patch32"


def load_clip():
    import torch
    from transformers import CLIPModel, CLIPProcessor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CLIPModel.from_pretrained(MODEL_ID).to(device)
    processor = CLIPProcessor.from_pretrained(MODEL_ID)
    return processor, model


def image_embedding(processor, model, image):
    """This transformers version's get_image_features returns a
    BaseModelOutputWithPooling (projected embedding in .pooler_output), not
    a bare tensor like older versions — unwrap it."""
    import torch
    device = next(model.parameters()).device
    inputs = processor(images=image, return_tensors="pt").to(device)
    with torch.no_grad():
        feats = model.get_image_features(**inputs).pooler_output
    return feats / feats.norm(dim=-1, keepdim=True)


def text_embedding(processor, model, text):
    import torch
    device = next(model.parameters()).device
    inputs = processor(text=[text], return_tensors="pt", padding=True, truncation=True).to(device)
    with torch.no_grad():
        feats = model.get_text_features(**inputs).pooler_output
    return feats / feats.norm(dim=-1, keepdim=True)


def cosine_similarity(a, b) -> float:
    return float((a @ b.T).squeeze().item())


def unload_clip(processor, model):
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
