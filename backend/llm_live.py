"""Explicit, bounded DeepSeek calls. Credentials stay in process memory only."""
import asyncio
import json
import httpx
from uuid import UUID
from pydantic import Field
from .llm_bridge import PreviewRequest

ENDPOINT = 'https://api.deepseek.com/chat/completions'


class SendRequest(PreviewRequest):
    request_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    call_id: UUID


class DeepSeekSession:
    def __init__(self):
        self._key = ''
        self.lock = asyncio.Lock()
        self.attempted = set()

    def status(self):
        return {'configured': bool(self._key), 'storage': 'server_memory_only',
                'endpoint': ENDPOINT, 'model': 'deepseek-flash'}

    def configure(self, key):
        if not isinstance(key, str) or not 10 <= len(key.strip()) <= 512 or not key.strip().isascii() or any(c.isspace() for c in key.strip()):
            raise ValueError('Enter a valid API key without spaces. The key was not stored.')
        self._key = key.strip()
        return self.status()

    def clear(self):
        self._key = ''
        return self.status()

    async def send(self, body):
        if not self._key:
            raise ValueError('Set your API key under DeepSeek connection first.')
        # Fixed HTTPS destination, no redirects, retries, proxy environment or tools.
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10), follow_redirects=False, trust_env=False) as client:
                async with client.stream('POST', ENDPOINT, headers={'Authorization': 'Bearer '+self._key}, json=body) as response:
                    if response.status_code != 200:
                        messages = {401:'API key rejected. Check or replace it.', 402:'DeepSeek balance is insufficient.',
                                    429:'DeepSeek rate limit reached. No automatic retry was made.'}
                        raise ValueError(messages.get(response.status_code, f'DeepSeek returned HTTP {response.status_code}. No automatic retry was made.'))
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > 1024*1024:
                            raise ValueError('DeepSeek response exceeded the local size limit. The request may have been billed.')
            result = json.loads(chunks)
            if not isinstance(result, dict):
                raise ValueError('DeepSeek returned an invalid response envelope.')
            return result
        except httpx.TimeoutException:
            raise ValueError('DeepSeek timed out. It may have processed and billed the request. No automatic retry was made.') from None
        except httpx.HTTPError:
            raise ValueError('Could not complete the DeepSeek connection. Billing status is unknown. No automatic retry was made.') from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError('DeepSeek returned invalid JSON. No automatic retry was made.') from None


def completion(result):
    """Never treat truncated, refused, tool-call or empty output as a valid plan."""
    usage = {k: v for k, v in (result.get('usage') or {}).items()
             if k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
             and isinstance(v, int) and not isinstance(v, bool) and v >= 0} if isinstance(result.get('usage'), dict) else {}
    choices = result.get('choices')
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return None, usage, 'Unexpected response choices; no plans imported.'
    choice = choices[0]
    message = choice.get('message')
    if choice.get('finish_reason') != 'stop' or not isinstance(message, dict) or message.get('refusal') or message.get('tool_calls'):
        return None, usage, 'Response was incomplete, refused or requested tools; no plans imported.'
    content = message.get('content')
    if not isinstance(content, str) or not content.strip() or len(content) > 50000:
        return None, usage, 'Response was empty or oversized; no plans imported.'
    return content, usage, None
