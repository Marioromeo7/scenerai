"""
Standalone Groq content test — NOT part of the engine, NOT wired into the
app. Sends whatever text you provide straight to Groq's chat completions
API using your own GROQ_API_KEY (from .env) and prints the raw response.

This exists so you can test what Groq's models will and won't generate
directly, with content you supply yourself at runtime -- nothing here is
pre-written or content-specific.

Usage:
    python scripts/test_groq_direct.py "your scenario description here"
    python scripts/test_groq_direct.py --model openai/gpt-oss-120b "..."
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from groq import Groq


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prompt", help="the scenario/prompt text to send to Groq, as-is")
    p.add_argument("--model", default="openai/gpt-oss-120b", help="Groq model ID")
    p.add_argument("--system", default="", help="optional system prompt")
    p.add_argument("--max-tokens", type=int, default=800)
    args = p.parse_args()

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        print("GROQ_API_KEY not set -- check .env at project root", file=sys.stderr)
        sys.exit(1)

    client = Groq(api_key=api_key)
    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.prompt})

    resp = client.chat.completions.create(
        model=args.model,
        messages=messages,
        max_tokens=args.max_tokens,
    )
    choice = resp.choices[0]
    print(f"--- finish_reason: {choice.finish_reason} ---")
    print(choice.message.content)


if __name__ == "__main__":
    main()
