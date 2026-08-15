"""
Local Stable Diffusion 1.5 wrapper — Section 4.3 of NEXT_PHASE_PLAN.md.
CreativeML OpenRAIL-M licensed, confirmed commercial-use permitting.

VRAM budget here is genuinely tight: RTX 3050 Laptop, 4GB total, ~2.3GB
free with nothing else running. fp16 + attention/VAE slicing gets a single
SD1.5 pipeline down to a workable footprint; sequential CPU offload is
available as a fallback if that's still not enough, at a real latency cost.
Never load this alongside the captioning/CLIP models in the same process —
run_experiment.py loads one at a time and frees between stages.
"""
import os
import torch

# All HF downloads/cache go to D: — this machine's C: drive is nearly full
# (see SESSION_ADVICE.txt) and a 4GB SD1.5 checkpoint would refill it fast.
_HF_CACHE = os.path.join(os.path.dirname(__file__), ".hf_cache")
os.environ.setdefault("HF_HOME", _HF_CACHE)
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", _HF_CACHE)

MODEL_ID = "stable-diffusion-v1-5/stable-diffusion-v1-5"
IMAGE_SIZE = 512  # SD1.5's native resolution — anything larger degrades quality without a hires pass


def load_pipeline(low_vram=True):
    from diffusers import StableDiffusionPipeline, DPMSolverMultistepScheduler

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    # Keep the default safety_checker enabled — do NOT strip it for speed,
    # which many quickstart tutorials do (plan's explicit decision, 4.4).
    # from_pretrained loads it by default as long as safety_checker isn't
    # passed at all.
    pipe = StableDiffusionPipeline.from_pretrained(MODEL_ID, torch_dtype=dtype)
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)

    if device == "cuda":
        if low_vram:
            pipe.enable_attention_slicing()
            pipe.enable_vae_slicing()
        pipe = pipe.to(device)
    return pipe


def generate(pipe, prompt, negative_prompt, seed, steps=25, guidance_scale=7.5):
    """Deterministic given (prompt, negative_prompt, seed) — same inputs,
    same output, which is exactly what the seed-based consistency mechanism
    (plan 4.1) depends on."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    generator = torch.Generator(device=device).manual_seed(seed)
    result = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt,
        num_inference_steps=steps,
        guidance_scale=guidance_scale,
        height=IMAGE_SIZE, width=IMAGE_SIZE,
        generator=generator,
    )
    return result.images[0]


def unload_pipeline(pipe):
    """.to("cpu") frees the GPU-resident tensors immediately, regardless of
    whether the caller's own `pipe` variable still references this object
    (del here only drops OUR local binding, not theirs — relying on
    refcount hitting zero silently no-ops and leaves VRAM occupied)."""
    import gc
    if torch.cuda.is_available():
        pipe.to("cpu")
    del pipe
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
