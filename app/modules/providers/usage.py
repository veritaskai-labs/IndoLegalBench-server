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
    """Pilih bentuk dari isi body. Dipakai bila jalur HTTP belum tahu penyedianya.

    Urutan pembaca adalah strateginya: bentuk pertama yang cocok menang, karena
    beberapa penyedia memakai nama field yang sama.
    """
    usage = payload.get("usage")
    for matches, reader in _READERS:
        if matches(payload, usage):
            return reader(payload)
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
    cached, write = _openai_cache_details(usage)
    return TokenUsage(
        uncached_input=_subtract(prompt, cached, write),
        cache_read=cached,
        cache_write=write,
        output=completion,
    )


def normalize_anthropic(payload: dict) -> TokenUsage:
    """input_tokens tidak memuat cache. Tingkatan tulis menggantikan jumlahnya bila lengkap."""
    usage = _usage(payload)
    if usage is None:
        return TokenUsage()
    write, write_long = _anthropic_writes(usage)
    return TokenUsage(
        uncached_input=_count(usage.get("input_tokens")),
        cache_read=_count(usage.get("cache_read_input_tokens")),
        cache_write=write,
        cache_write_long=write_long,
        output=_count(usage.get("output_tokens")),
    )


def normalize_gemini(payload: dict) -> TokenUsage:
    """Interactions atau usageMetadata. Token berpikir ditambahkan ke keluaran."""
    usage = _gemini_usage(payload)
    if usage is None:
        return TokenUsage()
    prompt = _first_count(usage, "total_input_tokens", "promptTokenCount")
    cached = _first_count(usage, "total_cached_tokens", "cachedContentTokenCount")
    output = _first_count(usage, "total_output_tokens", "candidatesTokenCount")
    thoughts = _first_count(usage, "total_thought_tokens", "thoughtsTokenCount")
    return TokenUsage(
        uncached_input=_subtract(prompt, cached),
        cache_read=cached,
        output=_add(output, thoughts),
    )


def _looks_like_gemini(payload: dict, usage: object) -> bool:
    if isinstance(usage, dict) and _has_any(usage, "total_input_tokens", "total_output_tokens"):
        return True
    return "usageMetadata" in payload


def _looks_like_openai(_payload: dict, usage: object) -> bool:
    return isinstance(usage, dict) and _has_any(
        usage, "prompt_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"
    )


def _looks_like_anthropic(_payload: dict, usage: object) -> bool:
    return isinstance(usage, dict) and _has_any(usage, "input_tokens", "output_tokens")


_READERS = (
    (_looks_like_gemini, normalize_gemini),
    (_looks_like_openai, normalize_openai),
    (_looks_like_anthropic, normalize_anthropic),
)


def _has_any(usage: dict, *keys: str) -> bool:
    return any(key in usage for key in keys)


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


def _openai_cache_details(usage: dict) -> tuple[int | None, int | None]:
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        return None, None
    return _count(details.get("cached_tokens")), _count(details.get("cache_write_tokens"))


def _anthropic_writes(usage: dict) -> tuple[int | None, int | None]:
    """Tingkatan yang lengkap menang. Jumlah nol dengan total terisi tetap memakai total."""
    write_total = _count(usage.get("cache_creation_input_tokens"))
    short, long = _write_tiers(usage.get("cache_creation"))
    if short is None and long is None:
        return write_total, None
    tier_sum = (short or 0) + (long or 0)
    if write_total is not None and tier_sum == 0 and tier_sum != write_total:
        return write_total, None
    return short, long


def _gemini_usage(payload: dict) -> dict | None:
    usage = _usage(payload)
    if usage is not None:
        return usage
    metadata = payload.get("usageMetadata")
    if isinstance(metadata, dict):
        return metadata
    return None


def _first_count(usage: dict, *keys: str) -> int | None:
    for key in keys:
        count = _count(usage.get(key))
        if count is not None:
            return count
    return None


def _subtract(total: int | None, *parts: int | None) -> int | None:
    if total is None or all(part is None for part in parts):
        return total
    return max(total - sum(part or 0 for part in parts), 0)


def _add(left: int | None, right: int | None) -> int | None:
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)
