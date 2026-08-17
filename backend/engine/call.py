import re
import time
import asyncio
import logging
import contextvars
from groq import Groq, AsyncGroq

logger = logging.getLogger(__name__)

# Per-async-task context vars — each FastAPI request gets its own isolated client.
# asyncio.to_thread() copies the context, so engine threads also see the right client.
_client_var:       contextvars.ContextVar = contextvars.ContextVar('groq_client')
_async_client_var: contextvars.ContextVar = contextvars.ContextVar('groq_async_client')
_model_var:        contextvars.ContextVar = contextvars.ContextVar('groq_model', default='openai/gpt-oss-20b')

SUMMARY_EVERY = 8
MAX_RAW_TURNS = 14


def init_client(api_key: str, model: str = 'openai/gpt-oss-20b'):
    _client_var.set(Groq(api_key=api_key))
    _async_client_var.set(AsyncGroq(api_key=api_key))
    _model_var.set(model)


def _log_call(model, max_tokens, temp, latency, system, history, response):
    last_user = next((m['content'] for m in reversed(history) if m['role'] == 'user'), '')
    logger.info(
        '[LLM] model=%-28s tokens=%-4d temp=%.2f latency=%.2fs | sys="%s" → "%s"',
        model, max_tokens, temp, latency,
        system[:80].replace('\n', ' '),
        response[:80].replace('\n', ' '),
    )
    logger.debug(
        '[LLM FULL]\nSYSTEM:\n%s\n\nLAST USER:\n%s\n\nRESPONSE:\n%s',
        system, last_user, response,
    )


def call(system, history, max_tokens=600, temp=0.82, retries=6):
    client = _client_var.get(None)
    model  = _model_var.get()
    if client is None:
        raise RuntimeError('Engine client not initialized. Call init_client() first.')
    for attempt in range(retries):
        try:
            t0 = time.time()
            r  = client.chat.completions.create(
                model=model,
                messages=[{'role': 'system', 'content': system}, *history],
                max_tokens=max_tokens,
                temperature=temp,
            )
            latency  = round(time.time() - t0, 2)
            response = r.choices[0].message.content.strip()
            _log_call(model, max_tokens, temp, latency, system, history, response)
            return response, latency
        except Exception as e:
            msg = str(e).lower()
            is_rate = (
                '429' in str(e) or
                'rate limit' in msg or
                'rate_limit_exceeded' in msg or
                'tokens per minute' in msg or
                'requests per minute' in msg
            )
            if not is_rate:
                logger.error('[LLM] call failed (non-rate): %s', e)
                raise
            wait   = 65.0
            ms_m   = re.search(r'try again in ([\d.]+)ms', str(e))
            s_m    = re.search(r'try again in ([\d.]+)s',  str(e))
            if ms_m: wait = float(ms_m.group(1)) / 1000 + 0.1
            elif s_m: wait = float(s_m.group(1)) + 0.1
            if attempt < retries - 1:
                logger.warning('[LLM] rate limit — waiting %.1fs (attempt %d/%d)', wait, attempt + 1, retries)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError('call() exhausted all retries')


async def async_call(system, history, max_tokens=600, temp=0.82, retries=6):
    """Async wrapper — runs sync call() in a thread pool to avoid blocking the event loop."""
    return await asyncio.to_thread(call, system, history, max_tokens, temp, retries)


async def async_stream_call(system, history, max_tokens=600, temp=0.82):
    """Async generator — streams tokens from Groq API using the async client."""
    async_client = _async_client_var.get(None)
    model        = _model_var.get()
    if async_client is None:
        raise RuntimeError('Engine client not initialized. Call init_client() first.')
    logger.info('[LLM STREAM] model=%-28s tokens=%-4d temp=%.2f | sys="%s"',
                model, max_tokens, temp, system[:80].replace('\n', ' '))
    t0 = time.time()
    collected = []
    try:
        stream = await async_client.chat.completions.create(
            model=model,
            messages=[{'role': 'system', 'content': system}, *history],
            max_tokens=max_tokens,
            temperature=temp,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                collected.append(delta)
                yield delta
        response = ''.join(collected)
        latency  = round(time.time() - t0, 2)
        logger.info('[LLM STREAM] done — latency=%.2fs tokens_out=%d | → "%s"',
                    latency, len(collected), response[:80].replace('\n', ' '))
        logger.debug('[LLM STREAM FULL]\nRESPONSE:\n%s', response)
        yield None  # sentinel: stream complete
    except Exception as e:
        logger.error('[LLM STREAM] failed after %.2fs: %s', time.time() - t0, e)
        yield None
        raise
