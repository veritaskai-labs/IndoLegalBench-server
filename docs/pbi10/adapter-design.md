## Desain adapter

Kontrak yang sudah ada dipertahankan: ABC `ProviderAdapter` dengan `name`, `test_connection()`, `ask(prompt)`, dan `ProviderResponse`. Konfigurasi diberikan lewat constructor, jadi method tetap tanpa argumen konfigurasi.

Satu usulan perubahan: `test_connection()` saat ini mengembalikan `bool`, sehingga alasan gagal dan versi model hilang, padahal UC-09 butuh `recordModelVersion()` dan UI butuh pesan gagal. Usulannya `test_connection()` mengembalikan `ProviderResponse` juga. Karena ini mengubah kontrak, perlu disetujui oleh Rafa.

```text
app/modules/providers/
├── router.py / service.py / repository.py / models.py / schemas.py
├── crypto.py                 encrypt_credential / decrypt_credential (Fernet)
└── adapters/
    ├── __init__.py           build_adapter(product) → ProviderAdapter  (registry)
    ├── base.py               ProviderAdapter, ProviderResponse, AdapterConfig, kode error
    ├── openai_compatible.py  GPT, DeepSeek, Gemini, Claude
    ├── custom_http.py        endpoint non-OpenAI via request_config
    └── aiyu.py               kalau AiYU butuh logika di luar custom_http
```

```python
# adapters/base.py — tambahan terhadap file yang sudah ada
@dataclass(frozen=True)
class AdapterConfig:
    base_url: str
    model_name: str
    auth_header_name: str
    auth_scheme: str | None
    credential: str            # sudah didekripsi, hanya di memori
    request_config: dict | None = None
    timeout_s: float = 15.0

# Nilai untuk ProviderResponse.error
AUTH_FAILED = "auth_failed"
MODEL_NOT_FOUND = "model_not_found"
RATE_LIMITED = "rate_limited"
TIMEOUT = "timeout"
NETWORK_ERROR = "network_error"
INVALID_RESPONSE = "invalid_response"
UPSTREAM_ERROR = "upstream_error"

class ProviderAdapter(ABC):
    name: str

    def __init__(self, config: AdapterConfig) -> None:
        self.config = config

    @abstractmethod
    def test_connection(self) -> ProviderResponse: ...   # usulan: sebelumnya bool

    @abstractmethod
    def ask(self, prompt: str) -> ProviderResponse: ...
```

```python
# adapters/__init__.py — registry (Strategy + Factory)
_ADAPTERS: dict[ProviderType, type[ProviderAdapter]] = {
    ProviderType.OPENAI_COMPATIBLE: OpenAICompatibleAdapter,
    ProviderType.CUSTOM_HTTP: CustomHttpAdapter,
}

def build_adapter(provider_type: ProviderType, config: AdapterConfig) -> ProviderAdapter:
    return _ADAPTERS[provider_type](config)
```

`build_adapter` menerima tipe dan config, bukan model `AiProduct`, supaya adapter tidak bergantung pada tabel. Service yang mendekripsi kredensial dan menyusun `AdapterConfig`.

### `openai_compatible`

| Aspek | Nilai |
| --- | --- |
| Request | `POST {base_url}/chat/completions` |
| Header | `{auth_header_name}: {auth_scheme} {credential}` |
| Body | `{"model": model_name, "messages": [{"role": "user", "content": prompt}]}` |
| `ProviderResponse.text` | `choices[0].message.content` |
| `ProviderResponse.model_version` | field `model` dari response |
| `ProviderResponse.raw` | JSON response utuh (disimpan runner ke `executions.raw_response`) |
| `test_connection()` | `ask("ping")` dengan `max_tokens: 1` |

### `custom_http`

Format dideklarasikan di `request_config` dan divalidasi Pydantic saat produk disimpan:

```json
{
  "method": "POST",
  "path": "/v1/messages",
  "extra_headers": { "anthropic-version": "2023-06-01" },
  "body_template": {
    "model": "{{model}}",
    "max_tokens": 1024,
    "messages": [{ "role": "user", "content": "{{prompt}}" }]
  },
  "response_text_path": "content[0].text",
  "response_model_path": "model"
}
```

### Pemetaan error

| Kondisi | `ProviderResponse.error` | Perlakuan runner |
| --- | --- | --- |
| 401, 403 | `auth_failed` | Hentikan produk ini, catat error |
| 404 | `model_not_found` | Hentikan produk ini, catat error |
| 429 | `rate_limited` | Retry dengan backoff (G4) |
| 5xx | `upstream_error` | Retry dengan backoff (G4) |
| Timeout | `timeout` | Retry dengan backoff (G4) |
| 2xx tanpa jawaban | `invalid_response` | Catat error, simpan raw |

Sesuai docstring `ProviderResponse`, error tetap dikembalikan sebagai data, bukan exception, sehingga runner mencatatnya sebagai `executions.status = error` (G5).

### Design pattern (menjawab feedback Pak Edy)

| Pattern | Di mana | Alasan |
| --- | --- | --- |
| Adapter | `openai_compatible.py`, `custom_http.py`, `aiyu.py` | Menerjemahkan format tiap produk ke satu `ProviderResponse`, jadi runner dan scoring tidak tahu detail produk (SDS §9.3) |
| Strategy + Factory (registry) | `build_adapter()` | Adapter dipilih dari data `provider_type`, bukan if/else di runner. Produk baru cukup satu baris di tabel; protokol baru cukup satu kelas + satu entri registry |
| Repository | `providers/repository.py` | Query terpisah dari logika bisnis, sesuai aturan modul yang sudah ada; service bisa diuji dengan data SQLite |
| Service sebagai facade antarmodul | `providers.service.is_runnable()`, `get_adapter_for()` | Modul runs tidak menyentuh tabel `ai_products`, sama seperti `suites.service.is_exportable()` |
| Data-driven configuration | `request_config` | Variasi `custom_http` sebagai data tervalidasi, bukan kode baru per produk |
