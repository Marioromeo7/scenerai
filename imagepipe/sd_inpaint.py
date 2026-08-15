"""
SD1.5 inpainting wrapper — Phase B of the attribute-reliability work,
triggered because Phase A (retry-until-correct on the whole image) hit a
real ceiling: canonicalization made cloak_color worse, not better, and
eye_color stayed broken regardless of pose-constraint/crop-zoom.

The idea: instead of regenerating the WHOLE image and hoping a lucky seed
satisfies every attribute at once, mask just the region that failed its
check and regenerate only that patch, with a targeted prompt, keeping
everything already-correct pixel-for-pixel untouched. This should be more
sample-efficient than whole-image rejection sampling, since it only
re-rolls the part that's actually wrong.

Same CreativeML OpenRAIL-M license as the base SD1.5 checkpoint used
elsewhere in this pipeline — confirmed commercial-use permitting before
downloading.

hair_color/cloak_color still use heuristic-rectangle regions (same spirit
as attribute_check.crop_upper_region) — they're large enough (each covers
roughly half the frame) that pose variation rarely pushes them fully out
of bounds, and both already measured well (95%/85%) without detection.
eye_color uses real face_detect.py landmarks instead, since a direct
visual audit found the fixed-height-band guess missing the actual eyes in
5 of 7 sampled failures (profile poses, closed eyes, faces sitting
higher/lower in frame than assumed) — the narrow band made it far more
sensitive to pose drift than the two large regions.
"""
import os

_HF_CACHE = os.path.join(os.path.dirname(__file__), ".hf_cache")
os.environ.setdefault("HF_HOME", _HF_CACHE)
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", _HF_CACHE)

MODEL_ID = "stable-diffusion-v1-5/stable-diffusion-inpainting"

# Heuristic mask regions as (top_frac, bottom_frac) of image height, full
# width — matches the fixed "portrait, single character" composition used
# throughout this pipeline. Keyed by attribute_check.py's check "name".
# eye_color is a fallback ONLY (used when face_detect finds no face) —
# see build_mask().
MASK_REGIONS = {
    "hair_color": (0.0, 0.45),
    "cloak_color": (0.45, 1.0),
    "eye_color": (0.15, 0.35),
}


def load_pipeline(low_vram: bool = True):
    from diffusers import StableDiffusionInpaintPipeline
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    pipe = StableDiffusionInpaintPipeline.from_pretrained(MODEL_ID, torch_dtype=dtype)
    if device == "cuda":
        if low_vram:
            pipe.enable_attention_slicing()
            pipe.vae.enable_slicing()
        pipe = pipe.to(device)
    return pipe


def build_mask(image, attribute_name: str):
    """White (255) = regenerate this region, black (0) = keep untouched."""
    from PIL import Image, ImageDraw
    w, h = image.size
    mask = Image.new("L", (w, h), 0)

    if attribute_name == "eye_color":
        import face_detect
        box = face_detect.eye_mask_box(image)
        if box is not None:
            ImageDraw.Draw(mask).rectangle(list(box), fill=255)
            return mask
        # no face detected -- fall through to the heuristic band below
        # rather than skip the attempt; this matches the pre-2.0 behavior
        # for images the detector can't parse (e.g. very stylized art).

    top_frac, bottom_frac = MASK_REGIONS[attribute_name]
    top, bottom = int(h * top_frac), int(h * bottom_frac)
    ImageDraw.Draw(mask).rectangle([0, top, w, bottom], fill=255)
    return mask


def inpaint(pipe, image, attribute_name: str, prompt: str, negative_prompt: str,
            seed: int, steps: int = 20, guidance_scale: float = 7.5):
    """Regenerates only the masked region, returns the composited result.
    Deterministic given (image, mask, prompt, seed) — same seed-based
    contract as sd_pipeline.generate()."""
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    mask = build_mask(image, attribute_name)
    generator = torch.Generator(device=device).manual_seed(seed)
    result = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt,
        image=image,
        mask_image=mask,
        num_inference_steps=steps,
        guidance_scale=guidance_scale,
        generator=generator,
    )
    return result.images[0]


def unload_pipeline(pipe):
    """.to("cpu") frees GPU VRAM immediately regardless of lingering
    references elsewhere — see sd_pipeline.unload_pipeline for why plain
    del isn't enough."""
    import gc
    import torch
    if torch.cuda.is_available():
        pipe.to("cpu")
    del pipe
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
