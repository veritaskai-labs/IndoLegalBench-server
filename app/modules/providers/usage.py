"""Token pemakaian satu respons, dipisah supaya tidak dihitung dua kali.

OpenAI-compatible, Anthropic Messages, dan Gemini Interactions mengirim
bentuk yang berbeda. DeepSeek memakai bentuk Chat Completions yang sama
dengan OpenAI, jadi responsnya menguji jalur openai_compatible, bukan
adapter keempat. Angka yang hilang tetap None. Angka 0 tetap 0. Biaya
alat tidak dibaca.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenUsage:
    """Bagian token yang tidak saling tumpang tindih.

    input_tokens adalah jumlah bagian masukan yang dilaporkan. output adalah
    token keluaran yang ditagih, termasuk token berpikir Gemini yang berdiri
    sendiri. Token penalaran OpenAI sudah ada di dalam completion.
    """

    uncached_input: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None
    cache_write_long: int | None = None
    output: int | None = None

    @property
    def input_tokens(self) -> int | None:
        present = [
            part
            for part in (
                self.uncached_input,
                self.cache_read,
                self.cache_write,
                self.cache_write_long,
            )
            if part is not None
        ]
        if not present:
            return None
        return sum(present)

    @property
    def output_tokens(self) -> int | None:
        return self.output


def normalize_usage(payload: dict) -> TokenUsage:
    """Pilih bentuk dari isi body. Dipakai bila jalur HTTP belum tahu penyedianya."""
    usage = payload.get("usage")
    if isinstance(usage, dict) and (
        "total_input_tokens" in usage or "total_output_tokens" in usage
    ):
        return normalize_gemini(payload)
    if "usageMetadata" in payload:
        return normalize_gemini(payload)
    if isinstance(usage, dict) and (
        "prompt_tokens" in usage
        or "prompt_cache_hit_tokens" in usage
        or "prompt_cache_miss_tokens" in usage
    ):
        return normalize_openai(payload)
    if isinstance(usage, dict) and ("input_tokens" in usage or "output_tokens" in usage):
        return normalize_anthropic(payload)
    return TokenUsage()


def normalize_openai(payload: dict) -> TokenUsage:
    """Chat Completions untuk openai_compatible, termasuk DeepSeek.

    Hit dan miss, bila ada, menggantikan rincian cached_tokens supaya bagian
    cache yang sama tidak dikurangi dua kali. Token penalaran sudah termasuk
    di completion_tokens.
    """
    usage = _usage(payload)
    if usage is None:
        return TokenUsage()
    prompt = _count(usage.get("prompt_tokens"))
    completion = _count(usage.get("completion_tokens"))
    hit = _count(usage.get("prompt_cache_hit_tokens"))
    miss = _count(usage.get("prompt_cache_miss_tokens"))
    if hit is not None or miss is not None:
        return TokenUsage(
            uncached_input=miss if miss is not None else prompt,
            cache_read=hit,
            output=completion,
        )
    details = usage.get("prompt_tokens_details")
    cached = write = None
    if isinstance(details, dict):
        cached = _count(details.get("cached_tokens"))
        write = _count(details.get("cache_write_tokens"))
    uncached = prompt
    if prompt is not None and (cached is not None or write is not None):
        uncached = max(prompt - (cached or 0) - (write or 0), 0)
    return TokenUsage(
        uncached_input=uncached,
        cache_read=cached,
        cache_write=write,
        output=completion,
    )


def normalize_anthropic(payload: dict) -> TokenUsage:
    """input_tokens tidak memuat cache. Tingkatan tulis menggantikan jumlahnya bila lengkap."""
    usage = _usage(payload)
    if usage is None:
        return TokenUsage()
    write_total = _count(usage.get("cache_creation_input_tokens"))
    short, long = _write_tiers(usage.get("cache_creation"))
    if short is not None or long is not None:
        tier_sum = (short or 0) + (long or 0)
        if write_total is not None and tier_sum != write_total and tier_sum == 0:
            short, long = write_total, None
        write, write_long = short, long
    else:
        write, write_long = write_total, None
    return TokenUsage(
        uncached_input=_count(usage.get("input_tokens")),
        cache_read=_count(usage.get("cache_read_input_tokens")),
        cache_write=write,
        cache_write_long=write_long,
        output=_count(usage.get("output_tokens")),
    )


def normalize_gemini(payload: dict) -> TokenUsage:
    """Interactions atau usageMetadata. Token berpikir ditambahkan ke keluaran."""
    usage = _usage(payload)
    if usage is None and isinstance(payload.get("usageMetadata"), dict):
        usage = payload["usageMetadata"]
    if not isinstance(usage, dict):
        return TokenUsage()
    prompt = _count(usage.get("total_input_tokens"))
    if prompt is None:
        prompt = _count(usage.get("promptTokenCount"))
    cached = _count(usage.get("total_cached_tokens"))
    if cached is None:
        cached = _count(usage.get("cachedContentTokenCount"))
    output = _count(usage.get("total_output_tokens"))
    if output is None:
        output = _count(usage.get("candidatesTokenCount"))
    thoughts = _count(usage.get("total_thought_tokens"))
    if thoughts is None:
        thoughts = _count(usage.get("thoughtsTokenCount"))
    uncached = prompt
    if prompt is not None and cached is not None:
        uncached = max(prompt - cached, 0)
    billable = None
    if output is not None or thoughts is not None:
        billable = (output or 0) + (thoughts or 0)
    return TokenUsage(uncached_input=uncached, cache_read=cached, output=billable)


def _usage(payload: dict) -> dict | None:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    return usage if isinstance(usage, dict) else None


def _write_tiers(creation: object) -> tuple[int | None, int | None]:
    if not isinstance(creation, dict):
        return None, None
    return (
        _count(creation.get("ephemeral_5m_input_tokens")),
        _count(creation.get("ephemeral_1h_input_tokens")),
    )


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
