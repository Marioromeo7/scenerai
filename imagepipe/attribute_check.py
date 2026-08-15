"""
Attribute-level identity check via VQA — follow-up to Section 4.2's
whole-image/caption comparison, which turned out to be the wrong tool for
session consistency: generic BLIP captions never once mentioned eye color
or the scar across 3000 generations in the field-set experiment, and
whole-image CLIP similarity conflates pose/scene changes with identity
loss. For "does this attribute look right," you have to ask the image
directly rather than infer it from a caption or an embedding distance.

Salesforce/blip-vqa-base (~1.5GB, same size class as the captioning model
already in use) supports direct yes/no and short-answer questions, unlike
the base captioning model used elsewhere in this pipeline.

Per-field questions below are deliberately aimed at large, visually
obvious regions (hair, cloak) rather than eyes/scar — a pilot run showed
this model defaults to "black" for eye color regardless of actual image
content (tiny region, likely a training-data bias toward common colors),
so eye color and the scar are kept but treated as the "hard" attributes,
checked last, not the ones the incremental build should rely on first.
"""
import os

_HF_CACHE = os.path.join(os.path.dirname(__file__), ".hf_cache")
os.environ.setdefault("HF_HOME", _HF_CACHE)
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", _HF_CACHE)

MODEL_ID = "Salesforce/blip-vqa-base"


def load_vqa():
    import torch
    from transformers import BlipProcessor, BlipForQuestionAnswering

    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = BlipProcessor.from_pretrained(MODEL_ID)
    model = BlipForQuestionAnswering.from_pretrained(MODEL_ID).to(device)
    return processor, model


def ask(processor, model, image, question: str) -> str:
    import torch
    device = next(model.parameters()).device
    inputs = processor(image, question, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=15)
    return processor.decode(out[0], skip_special_tokens=True).strip().lower()


def crop_upper_region(image, top_frac: float = 0.5, center_frac: float = 0.6, zoom: int = 3):
    """Crops the upper-center of the frame and upscales it — a cheap,
    heuristic stand-in for face detection that works because every prompt
    in this pipeline fixes the composition ("portrait, single character").
    Same pixels the model already generated, just presented at higher
    effective resolution for the small VQA model to actually read, rather
    than a fresh independent draw (see run_incremental_build.py's
    docstring on why crop-then-verify only fixes the "too small to read"
    failure mode, not "the feature wasn't rendered at all")."""
    w, h = image.size
    crop_w = int(w * center_frac)
    crop_h = int(h * top_frac)
    left = (w - crop_w) // 2
    box = (left, 0, left + crop_w, crop_h)
    cropped = image.crop(box)
    return cropped.resize((crop_w * zoom, crop_h * zoom))


def unload_vqa(processor, model):
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


# Ordered so the incremental builder adds large/reliable-to-verify
# attributes first and the small/subtle ones last. "field" must match a
# key in visual_profile.EXAMPLE_PROFILE.
ATTRIBUTE_CHECKS = [
    {
        "field": "hair",
        "name": "hair_color",
        "question": "what color is the hair?",
        "expected_any": ["silver", "white", "grey", "gray"],
    },
    {
        "field": "clothing",
        "name": "cloak_color",
        "question": "what color is the cloak?",
        # "olive" alongside "green" — canonicalize_profile.py may rewrite
        # "dark green" to "olive" (its actual output for this profile), and
        # the check must accept whichever target color the prompt ends up
        # asking for, whether canonicalization is on or off.
        "expected_any": ["green", "olive"],
    },
    {
        "field": "eyes",
        "name": "eye_color",
        "question": "is the eye color grey, blue, green, or brown?",
        "expected_any": ["grey", "gray"],
        "crop": True,
    },
    {
        "field": "distinguishing_feature",
        "name": "scar_mark",
        "question": "does the character have a scar on their face?",
        "expected_any": ["yes"],
    },
]


def check_one(processor, model, image, check: dict) -> dict:
    target = crop_upper_region(image) if check.get("crop") else image
    answer = ask(processor, model, target, check["question"])
    match = any(exp in answer for exp in check["expected_any"])
    return {"answer": answer, "match": match}


def check_attributes(processor, model, image, checks=None) -> dict:
    checks = checks if checks is not None else ATTRIBUTE_CHECKS
    return {c["name"]: check_one(processor, model, image, c) for c in checks}
