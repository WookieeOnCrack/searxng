# Client Preferences Design — per-client search customization over `/api/v1`

**Date:** 2026-11-17
**Status:** implementation complete for §11 items 1–2; remaining follow-ups pending
**Scope:** how users / API clients customize their search experience: routes, request/response
schema, token format and signing, precedence between instance defaults and client overrides,
and how the `Preferences` object is populated per request.

## 1. The question

Every route in `backend/api/` is a GET. `GET /api/v1/preferences`
(`backend/api/preferences.py`) returns the *catalogue* — categories, engines, locales,
formats, `default`/`current`/`locked` — but it is deliberately stateless: `_build_preferences()`
passes `client_pref=None` and parses no client state, so `current == default` for every field.
There is no write route at all.

Two gaps decide the shape of the work:

1. **Nothing per-client reaches `Preferences`.** The search endpoint parses only its own query
   params; upstream's `POST /preferences` was never ported.
2. **`backend/api/search.py:216` passes `[]` as `user_plugins` to `SearchWithPlugins`.**
   Upstream (`searx/webapp.py:676`) passes `sxng_request.user_plugins`, which `pre_request`
   derives from the client's plugin preference. The empty list means **no plugin hook runs at
   all** in API responses today — including `tracker_url_remover` (active by default in
   `settings.yml`), so tracker arguments are never stripped from API results.

Four requirements to support:

| # | Requirement | Upstream mechanism |
|---|---|---|
| 1 | Enable/disable an engine *within a category* | `EnginesSetting`, keys `"{engine}__{category}"`, disabled as `(engine, category)` pairs (`searx/preferences.py:302-330`) |
| 2 | Enable/disable whole search types | `categories` (`MultipleChoiceSetting`, absolute value set — `preferences.py:418`) |
| 3 | Default search type | **Does not exist upstream** — fork-only extension |
| 4 | Disable tracker args in URLs | Not a preference — the `tracker_url_remover` plugin, `preference_section="privacy"`, opted out via the plugins preference |

Three of the four are "read a client preference document and feed it to `Preferences`".
Requirement 3 is new; requirement 4 rides the existing plugin mechanism.

## 2. Settled decisions (2026-11-17)

| Decision | Choice | Rationale |
|---|---|---|
| Durable state | **Signed client-held token** (option b) | Server stays stateless; no server-side profile |
| Server-side profile | **Rejected** (option c) | Changes the fork's privacy posture; upstream keeps no user profile either ("settings are stored on the user's computer", `docs/own-instance.rst`) |
| Share flow | **Dropped** | Fork is API/agent-first; the no-JS shareable-URL blob is not needed |
| Routes | `GET` + `POST /api/v1/preferences` | One document shape shared by the POST body, the search-query override and the token |
| Document | **Flat, upstream-compatible key names** | `Preferences.parse_dict()` consumes them with zero translation; `searx/` stays byte-identical |
| Envelope | `base64url(zlib(json))` [+ `"." + hex HMAC-SHA256`] | Same envelope as upstream's client blob (`get_as_url_params()`), plus signing |
| Transports | cookie · `X-SearXNG-Preferences` header · `?preferences=` param | One envelope, three carriers |
| Precedence | explicit request params > header > cookie > instance defaults | Request-level intent wins; defaults last |
| Merge order | instance defaults → client document → operator locks | Locks applied last, so they win |
| Signature | Optional; server-minted tokens signed + epoch-stamped | Unsigned = hand-editable/legacy; signed = staleness detection |
| `secret_key` | Add upstream's production guard to backend init | Default `ultrasecretkey` makes signatures forgeable |
| `default_category` | Fork-only, resolved in `backend/` | No upstream equivalent; keeps `searx/` untouched |
| Tracker toggle | `tracker_url_remover` plugin via `user_plugins` | Not a preference; plugin mechanism |
| POST semantics | **Replace** (the posted document *becomes* the client document); any presented token is ignored | See §10 Q1 |
| Validation | **Lenient application with a reported skip list**; `tokens` remains a hard rejection | Portable templates may carry keys another instance does not support |

## 3. Routes

### `GET /api/v1/preferences` (exists; gains client awareness)

- No client state present → `current == default` (as today).
- Client state present (header / cookie / `?preferences=`) → `current` reports the **effective
  merged values** for this client; `default` stays the instance defaults; `client` reports the
  client's own document (see §4); `locked` lists every non-changeable key.

### `POST /api/v1/preferences` (new)

Applies a preference document and returns the new effective state plus a minted token.

```http
POST /api/v1/preferences
Content-Type: application/json

{ "categories": ["general", "images"],
  "disabled_engines": ["google__general"],
  "enabled_engines": [],
  "disabled_plugins": ["tracker_url_remover"],
  "default_category": "general" }
```

```json
{ "token": "<signed token>",
  "current": { "...": "effective values after merge" },
  "default": { "...": "instance defaults" },
  "client":  { "categories": ["general"] },
  "skipped": ["google__general: engine pair is not available on this instance"],
  "plugins": [{ "id": "tracker_url_remover", "name": "Tracker URL Remover" }],
  "engine_pairs": ["google__general", "..."],
  "locked":  ["categories"] }
```

- Sets the `sxng_preferences` cookie (browser transport) **and** returns the token (header /
  param transport) — same bytes, two carriers.
- **Replace semantics** (Q1): the posted document becomes the entire new client document;
  nothing is merged from an incoming token. A token presented with POST is ignored and
  discarded; the body is the entire source of truth.
- **Lenient application:** every key whose value this instance can accept is applied. A key
  it cannot apply is skipped and reported in `skipped`. The only document-level rejection is
  `tokens` (`400 preferences_tokens_not_allowed`); a body that is not a JSON object is
  `400 preferences_invalid`.
- **Reset semantics:** POST with an empty document `{}` returns `client = {}` and mints a
  signed token of the empty document. There is deliberately no `DELETE` route: an API client
  resets by stopping to send its token, and a browser client resets by clearing its cookie.

## 4. Document schema

Flat, `Preferences.parse_dict()`-compatible keys — the server side is **one call**,
`preferences.parse_dict(doc)`, with zero translation:

| Key | Type | Notes |
|---|---|---|
| `categories` | array of category names | Enabled search types (`MultipleChoiceSetting`; absolute value set) |
| `disabled_engines` | array of `"engine__category"` | Per-category engine toggles — a **delta over instance defaults** |
| `enabled_engines` | array of `"engine__category"` | Applied **after** disabled (`parse_cookie` semantics) |
| `disabled_plugins` | array of plugin ids | Plugin opt-outs — a **delta over instance defaults** |
| `enabled_plugins` | array of plugin ids | Applied after disabled |
| `default_category` | string | **Fork-only.** Absent → `general`; present but unknown → skipped and reported |
| `safesearch`, `language`, `locale`, `theme`, … | string / scalar | `key_value_settings` passthrough (absolute values) |

**Excluded: `tokens`.** Engine API secrets must never ride in a portable blob — upstream's
`get_as_url_params()` includes them (`searx/preferences.py:524`); the fork must not copy that.
Posting `tokens` → **400** (`preferences_tokens_not_allowed`).

**Normalization.** The JSON wire format is JSON-native (arrays, bools); the backend normalizes
to the string forms `parse_dict` expects (`"1"`/`"0"` per `MAP_STR2BOOL`, comma-joined lists)
before the call. `MAP_STR2BOOL` accepts exactly: `0, 1, on, off, True, False, none`.

**Validation.** Documents are applied leniently: every key whose value this instance can
accept is applied; every key it cannot apply is skipped and reported in the response's
`skipped` list. This includes unknown keys, rejected values, unavailable engine pairs and
plugins, and an unknown `default_category`. `tokens` remains the one hard rejection
(`400 preferences_tokens_not_allowed`).

A typo'd preference key silently doing nothing is the same failure class as the `.time_range`
bug this fork already fixed. Under the lenient rule a typo is indistinguishable from a key
belonging to another instance, so the skip is always reported rather than silent.
`default_category` absent still means `general`.

Both engine lists are needed, not one: `parse_cookie` applies disabled-then-enabled, and 255
engines are disabled by default on this instance, so "enabled" is a real list.

## 5. Token format and signing

```
token     = payload [ "." signature ]
payload   = base64url( zlib( json({ "epoch": "<hex16>", "doc": { ...document... } }) ) )
signature = HMAC-SHA256(secret_key, payload)  → hexdigest, 64 chars
```

- The signature is **hex** (not base64) so the existing `searx.webutils.is_hmac_of`
  constant-time comparison can verify it — reuse `new_hmac`/`is_hmac_of` exactly as
  `backend/api/favicons.py` and `backend/api/images.py` already do.
- **Unsigned tokens are accepted** — a hand-edited or legacy blob keeps working; signing is an
  optional extra, not a requirement. Unsigned tokens carry no verifiable epoch.
- **Server-minted tokens are signed and epoch-stamped.** The epoch is a hash of the
  *interpretation base* for the document — the server-side state the client's delta lists are
  resolved against (see below). If the operator changes a default the client's document
  depends on, the epoch changes and the server **rejects a stale token with HTTP 409 +
  `error: "preferences_stale"`**; the client re-fetches. With the share flow dropped, staleness
  can hard-fail — no fallback URL to offer.
- **Signing gives integrity, not confidentiality.** The payload is plaintext to anything that
  can read it (proxy, WAF, client). Fine for preferences; engine `tokens` are excluded anyway.
- **Transports** (same bytes, different carrier):

| Transport | Where it rides | Attributes / notes |
|---|---|---|
| Cookie | `Cookie: ***` | `Path=/`, `Max-Age=157680000` (5y, matches upstream `COOKIE_MAX_AGE`), `HttpOnly`, `SameSite=Lax`, `Secure` over HTTPS |
| Header | `X-SearXNG-Preferences: <token>` | Agent / MCP / curl ergonomics; CORS-safe (`allow_headers=["*"]` already set) |
| Query param | `?preferences=<token>` | One-shot / share-by-link; URL length limits |

**Precedence** (highest wins): explicit per-request params on `/search` (including a
`?preferences=` token) **>** `X-SearXNG-Preferences` header **>** cookie **>** instance defaults.

### The epoch

`BooleanChoices.parse_cookie` mutates a map **seeded from instance defaults**
(`EnginesSetting`: `not engine.disabled` per expressible `engine__category` pair;
`PluginsSetting`: `plugin.active`), and `BooleanChoices.save` stores only the **delta** against
those defaults. So the epoch covers exactly that delta base, scoped to what the document
touches:

```
epoch = sha256( canonical_json({
    "engines": { "<engine>__<cat>": <default state>   # for each pair the document names },
    "plugins": { "<plugin>": <default state>           # for each plugin the document names },
    "locks":   [ ...sorted lock ids... ]
}) )[0:16]  # hex, truncated
```

The epoch is computed over the **applied (filtered) document**, not the submitted one. A
named engine pair or plugin the instance no longer has contributes `null`, so the recomputed
epoch differs and the token is rejected as stale (`409`) instead of causing an error.

Deliberately **excluded**: the engine/category roster (adding an engine does not change the
meaning of an existing delta entry) and the `key_value_settings` defaults (client values are
absolute, so a default change does not change what the client asked for). The lock set is
included because it changes enforcement. Net effect: an unrelated settings reload does not
invalidate tokens; changing the default state of an engine or plugin the client toggles does.
Because key/value-setting defaults are excluded, a setting value the instance drops is
skipped and reported rather than making the token stale.

**Error handling** (endpoints that consume a token: GET `/preferences` and, later, the
search route):

| Condition | Result |
|---|---|
| Malformed token (bad base64/zlib/JSON, wrong envelope) | **400** `preferences_invalid` |
| Signature present but does not verify | **403** `preferences_invalid_signature` |
| Signature valid, epoch stale | **409** `preferences_stale` |
| POST document carries `tokens` | **400** `preferences_tokens_not_allowed` |
| POST body is not a JSON object | **400** `preferences_invalid` |

POST consumes no token: a token presented with it is ignored and discarded. An unknown key or
bad value is skipped and reported, not rejected. No silent fallback to defaults: a silently
dropped preference produces plausible-looking wrong results — the same failure class as the
`time_range` bug this fork already fixed.

## 6. Merge and lock semantics

1. Start from instance defaults.
2. Apply the client document (`parse_dict`); locked `key_value_settings` keys are skipped by
   `parse_dict` itself.
3. Apply operator locks **last** — locks win over everything for locked keys.

### Fork-side engine/plugin locks

`preferences.lock` only reaches `key_value_settings`; `BooleanChoices.parse_cookie` never
checks `locked`, and `EnginesSetting`/`PluginsSetting` are constructed with no lock flag.
Fork-side enforcement, after parsing:

- **Config surface** (new keys under `preferences:` in `settings.yml`):
  ```yaml
  preferences:
    lock: [categories, safesearch]
    lock_engines: ["google__general"]     # this engine cannot be disabled in this category
    lock_plugins: ["tracker_url_remover"] # this plugin cannot be disabled
  ```
- **Enforcement:** re-assert the default state for locked entries after `parse_dict` — a
  locked engine pair is removed from the client's `disabled_engines`; a locked plugin is
  removed from `disabled_plugins`.
- **Error behavior:** **silent skip** — same convention as upstream's `parse_dict` skipping
  locked `key_value_settings` keys. A 400 would break clients that don't know about the lock.
  The locked ids are reported in `locked` so the UI can render non-changeable toggles.

## 7. How the `Preferences` object gets populated per request

1. Build `Preferences` exactly as `_build_preferences()` does today
   (`Preferences(themes, categories, engines, plugins_storage, client_pref)`).
2. Resolve the client document in precedence order (params > header > cookie); decode
   `?preferences=` as a document. Verify signature/epoch per §5 (errors per the table above).
3. `preferences.parse_dict(doc)` — upstream semantics; locked keys skipped; engine/plugin
   lists applied disabled-then-enabled.
4. **Fork-only `default_category`:** read from the doc, validate against the known categories,
   fall back to `general` when absent; resolve in `backend/` during category selection — never
   patched into `searx/preferences.py`.
5. **`user_plugins`:** mirror `webapp.pre_request` (`searx/webapp.py:535-540`) — derive
   `sxng_request.user_plugins` from `preferences.plugins.get_enabled()/get_disabled()` and pass
   it to `SearchWithPlugins` at `backend/api/search.py:216`, replacing the `[]`. This is what
   activates `tracker_url_remover` and the other plugins in API responses.
6. **Category resolution order:** request `categories` param → client `categories`
   preference → `default_category` → `['general']`. A locked `categories` preference wins over
   `default_category` (the lock is applied last). A bang query (`!engine` / `!category`)
   explicitly selects engines and ignores the default category — upstream semantics.
7. **Stale signed token:** HTTP 409 + `preferences_stale`; the client recovers by POSTing a
   fresh document, which works because POST ignores and discards the stale token.

## 8. Sizing and format measurements (this instance)

Measured 2026-11-17 against the live instance (353 engines, 377 `engine__category` pairs,
33 categories):

| Document | Raw | base64(zlib) | Verdict |
|---|---|---|---|
| Full document | 7,150 B | **2,636 B** | fits one 4 KB cookie |
| Upstream delta (`disabled_engines` only) | 4,762 B | **6,352 B** | exceeds one cookie — why upstream splits disabled/enabled |
| Typical client delta (20 engines) | 354 B | 472 B | — |
| Signature (hex HMAC-SHA256) | — | +64 chars | — |

Token-gated engines on this instance: **0** (token gates exist in the test settings; engine
secrets must not enter the blob regardless).

Re-measure with `scripts/measure_preferences.py` after implementation.

## 9. Risks and prerequisites

1. **`secret_key` guard.** The backend never runs upstream's production check
   (`searx/webapp.py:1380` exits when `server.secret_key` is still `ultrasecretkey`), and
   `searx/settings.yml` ships that value. Signatures are forgeable until the backend enforces
   the same guard in `init_kernel()`.
2. **CORS.** `backend/app.py` now enables `allow_credentials=True` so the SPA retains the
   cookie transport; `allow_methods` contains only `GET` and `POST`, because there is no
   DELETE route. `SameSite=Lax` is sufficient
   for same-site deployments (localhost:5173 → localhost:8080 is same-site; ports don't affect
   the site) as long as the request is credentialed; a cross-site SPA/API split needs
   `SameSite=None; Secure` (HTTPS only) — an operator decision, documented, not the default.
3. **Operator locks don't cover engines/plugins** by default — the new
   `lock_engines`/`lock_plugins` keys are opt-in and empty unless configured.
4. **Response contract grows:** `current` semantics change once client state exists (`current`
   diverges from `default`), and the new `client` field is added. `key_value_settings` covers
   `categories` (it is a `MultipleChoiceSetting` in that dict); the engine/plugin state is
   **not** in `key_value_settings`, so it needs the separate `client` field.
5. **`searx/` stays byte-identical** — all fork logic lives in `backend/`.

## 10. Builder check (2026-11-17)

A fresh reader (no access to the author) read this document against the repo and came back
with these questions. Resolutions recorded below; the three marked **open for override** are
the most product-flavored calls.

| # | Question | Resolution | Open for override |
|---|---|---|---|
| 1 | Does POST replace or merge with existing client state? | **Replace.** The posted document becomes the entire new client document; nothing is merged from an incoming token. The document is a delta over instance defaults, so "merge" has no well-defined semantics for the disabled/enabled lists (they are applied over defaults, not over the client's previous state). Round-trip: GET returns the document in `client`, the client edits and POSTs it back — one code path, no diffing. | **Yes** |
| 2 | What goes into the epoch, and does it gate all transports? | Scoped hash of the delta base over the **applied** document: default state of the engine pairs and plugins it names, plus the lock set; excludes the roster and key/value-setting defaults (see §5). Gated on signed tokens only — unsigned tokens carry no verifiable epoch. Every endpoint that consumes a token honors 409; POST consumes no token. | no |
| 3 | What happens on malformed / badly signed / stale tokens? | 400 `preferences_invalid` / 403 `preferences_invalid_signature` / 409 `preferences_stale`; validation failures 400. No silent fallback to defaults. | no |
| 4 | What must `current` / `locked` contain? | `current` = effective merged `key_value_settings` values (includes `categories`). New `client` field = the document exactly as POST accepts it (round-trippable); engine/plugin state lives there, not in `current`. `locked` = key_value lock names **plus** fork-side locked engine pairs / plugin ids. | no |
| 5 | DELETE semantics for non-cookie transports? | **Withdrawn:** there is no DELETE route. POST `{}` mints a signed empty-document token; header/param clients may also stop sending their token, and browser clients may clear the cookie. | no |
| 6 | Cookie attributes? | `sxng_preferences`; `Path=/`; `Max-Age=157680000` (upstream `COOKIE_MAX_AGE`, 5y); `HttpOnly`; `SameSite=Lax`; `Secure` over HTTPS. CORS gains `allow_credentials=True`. Cross-site split → `SameSite=None; Secure` (documented operator choice). | **Yes** (SameSite policy) |
| 7 | How are fork-side locks expressed and enforced? | New `preferences.lock_engines` / `lock_plugins` keys; enforced in `backend/` after parsing (re-assert defaults for locked entries); **silent skip**, reported in `locked` (upstream `parse_dict` convention). | no |
| 8 | POST handling of `tokens` / unknown keys / bad `default_category`? | `tokens` → 400 `preferences_tokens_not_allowed`. Unknown keys, rejected values, unavailable engines/plugins and unknown `default_category` are skipped and reported in `skipped`; absent `default_category` → `general`. POST ignores any presented token. | **Yes** (leniency) |
| 9 | Does `default_category` interact with locks/bangs? | Validated against all known categories; a locked `categories` preference wins over it; bangs win (explicit selection); no implicit fallback when the default category's engines are all disabled — the client's choice is honored and `engines_deactivated` shows why. | no |

Minor note (resolved): the doc's earlier 43-char base64 signature was reconciled to a **hex**
digest so `searx.webutils.is_hmac_of` (constant-time compare, hex) can verify it — matching the
existing favicon/image proxy convention.

## 11. Follow-ups (implementation order)

1. **Implemented** (`2df12787f..566311f7c`): `backend/api/preferences.py` adds POST, token
   minting/verification (reuse `searx.webutils.new_hmac`/`is_hmac_of`), epoch computation, and
   lenient document validation with a reported skip list. There is no DELETE route.
2. **Implemented** (`3de821528..566311f7c`): `backend/models/api.py` adds `client`, `token`,
   `skipped`, `plugins`, and `engine_pairs` fields to the response model.
3. `backend/api/search.py`: populate `user_plugins`; wire `default_category`; accept
   `?preferences=` as a per-request override; token error mapping (400/403/409).
4. `backend/services/kernel.py`: add the `secret_key` production guard.
5. `backend/app.py`: CORS — `allow_credentials=True`; `allow_methods` remains `GET`, `POST`.
6. Tests: token round-trip, precedence, stale-epoch 409, lock enforcement, lenient validation
   with a reported skip list (unit + regression suites).
7. `scripts/measure_preferences.py`: re-measure document sizes.

---

*Companion note in the Obsidian vault: [[SearXNG Client Preferences Design (11-17)]]*
