# Client Preferences Endpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `/api/v1/preferences` a write route — `POST` (apply a client preference document) — backed by a signed, epoch-stamped, client-held token, with lenient document application and a shared service module the search route will reuse.

**Architecture:** All fork logic lives in `backend/`; `searx/` stays byte-identical to upstream. A new `backend/services/preferences.py` owns the reusable pieces — building a `Preferences` instance, filtering/normalizing a client document, minting/verifying tokens, computing epochs, and reading the client token from a request. `backend/api/preferences.py` keeps only HTTP concerns (routing, status codes, cookies, response assembly). Upstream `Preferences.parse_dict()` remains the single place where preference semantics live; the service normalizes the JSON wire format into the string form `parse_dict()` expects.

**Tech Stack:** Python 3.14, FastAPI, Pydantic v2, upstream `searx.preferences` / `searx.webutils`, nose2 test runner.

**Spec:** `docs/superpowers/specs/2026-11-17-client-preferences-design.md` (vault copy: `/opt/data/obsidian/SearXNG Client Preferences Design (11-17).md`). The spec is the binding authority and this plan is its argument — but **two of its decisions were changed by the user on 2026-11-19** (see **Decisions taken** below): `DELETE` is dropped, and invalid documents are applied leniently rather than rejected. Those two changes are now the authority; the plan's job is to carry them out and to amend the spec to match.

## Scope

**In scope (this plan):** spec §11 items 1 and 2, as amended — `backend/api/preferences.py` POST + token minting + epoch computation + lenient document application, and the `client` / `token` fields on the response model.

**In scope (pulled forward, required by the above):** `backend/app.py` CORS (credentials, so the browser keeps the cookie) so the new route is callable from the SPA; consolidation of the four duplicated `_build_preferences()` copies into the new service module; the spec/§11 status update and the amendment of the two changed decisions.

**Out of scope (later spec §11 items, tracked in the spec):** item 3 (`backend/api/search.py`: `user_plugins`, `default_category` resolution, `?preferences=` per-request override, token error mapping), item 4 (`backend/services/kernel.py` production `secret_key` guard — see **Blocking dependency** below), item 6 (`scripts/measure_preferences.py` re-measurement), spec §6 fork-side engine/plugin locks (`lock_engines` / `lock_plugins` — see **Open decisions**).

## Decisions taken

### 1. There is no `DELETE` route

Dropped by the user on 2026-11-19. The reasoning: for an API client the token lives in a header or query parameter, so the client can simply stop sending it — a reset is "drop the token", not "call an endpoint". For a browser client the cookie is the client's to clear. A `DELETE` route would add a third way to say the same thing.

Consequence for the spec: §3's `DELETE /api/v1/preferences` section is withdrawn, and so is the `token` = signed-empty-document part of it. The spec's §11 item 1 is amended accordingly in Task 7.

The one thing `DELETE` uniquely provided — a *signed token of the empty document* — is still reachable: `POST` with an empty document `{}` mints exactly that token and sets it as the cookie. So no capability is lost.

### 2. `POST` ignores any presented token

The user settled the question this plan had flagged as blocking: **a token sent alongside `POST` is ignored and discarded.** The request body is the entire source of truth for the new state.

This is consistent with the spec's own replace semantics (§3, §10 Q1: "the posted document becomes the entire new client document; nothing is merged from an incoming token") and it removes the deadlock the plan had flagged: a client holding a stale token can always recover by POSTing a fresh document, because the stale token is never inspected. Spec §5's error table and §10 Q2's "every endpoint that consumes a token honors 409" therefore need a matching amendment: `POST` does not consume a token, so the 400/403/409 table governs `GET` (and, later, the search route).

### 3. Invalid documents are applied leniently, not rejected

The user's choice between (A) returning an error and (B) applying only the preferences that are available: **option B**. A document naming an engine, plugin, category, or preference key this instance does not have is applied *as far as it goes*; the unavailable parts are skipped rather than failing the request.

The motivation is portability: a person or an agent can carry a template document across instances, and each instance applies the part that applies to it. This is the opposite of the spec's §4 rule ("Unknown keys → 400 ... A typo'd preference key silently doing nothing is the same failure class as the `.time_range` bug this fork already fixed").

**How leniency is bounded — this is the part that needs care.** "Skip what is unavailable" must not become "silently accept a typo", because that is exactly the failure the spec's §4 rule exists to prevent. The distinction the plan draws:

- **A whole key that this instance does not have** (`google__general` when there is no Google engine, `safesearch` when the instance has no such setting) → **skipped**. This is the portable-template case and the reason for leniency.
- **A key that exists but whose *value* is malformed for it** (`image_proxy: "yes"`, `language: "xx-INVALID"`, `safesearch: 5`) → **skipped as well**, for the same reason: a template authored elsewhere may carry a value this instance rejects.
- **A key the instance *does* have, given a value it accepts** → applied.

So the rule is uniform: **apply every key whose value this instance can accept; skip the rest.** There is no separate "unknown key" error class, because under option B there is no such thing as an unknown key — only keys this instance cannot apply.

The cost, stated plainly: a typo'd key is now indistinguishable from a key belonging to another instance. The response mitigates it by reporting what was skipped (§ **Leniency is reported, never silent** below). If the user later wants typos to be loud, the natural switch is a "strict" flag on the service — the plan's structure keeps that a one-function change.

**Leniency is reported, never silent.** The `POST` response reports which keys were skipped and why, so a client (or a human reading the response) can see that `google__general` did not apply here. This preserves the spec's intent — no silent no-op — while honoring the user's choice not to fail the request. A template consumer therefore still learns that a preference was dropped.

**`tokens` remains a hard rejection.** Posting `tokens` is still `400 preferences_tokens_not_allowed` (spec §4). It is the one exception to leniency, because it is not an unavailable preference but a *forbidden* one: engine API secrets must never ride in a portable blob, and a document that carried them would be exactly the leak the rule prevents. Skipping-and-reporting it would still accept a document containing secrets into the request path, and the response would echo their presence.

**The epoch follows the applied document, not the submitted one.** The epoch is computed over the pairs and plugins the document *names* (spec §5). Under leniency the applied document is a subset of the submitted one, so the epoch is computed over the applied subset — the same base the client's state will be interpreted against.

**A vanished engine is detected, not crashed on.** If an engine is removed from the instance after a token was minted, the token's document still names it, and the recomputed epoch differs — two ways, both verified:

- The pair is no longer in `prefs.engines.choices`, so the filter drops it and the epoch is computed over a document that no longer names it. The digest differs from the mint-time digest.
- The pair is still selectable but its default state is unavailable, so `compute_epoch` contributes `null` for it instead of raising `KeyError`. The digest differs again.

Either way the token is rejected as **stale (409)** rather than causing a `KeyError` (500). This is the spec's §5 intent — "changing the default state of an engine ... invalidates" — extended to the engine disappearing outright. A client recovers by POSTing a fresh document, which works because `POST` ignores the stale token (see §2).

## Global Constraints

- **`searx/` is byte-identical to upstream.** All new logic lives in `backend/`. Do not edit `searx/preferences.py`, `searx/webutils.py`, `searx/_settings.py`, or `searx/settings.yml`.
- **Document keys are flat and upstream-compatible** — exactly the names `Preferences.parse_dict()` understands, plus the fork-only `default_category`. No translation layer beyond normalizing JSON-native values into the string forms `parse_dict()` expects.
- **Wire format is JSON-native** (arrays of strings, JSON booleans, JSON integers). The service normalizes to upstream string forms *before* calling `parse_dict()`.
- **`tokens` is never allowed in a document.** Posting it is `400 preferences_tokens_not_allowed` — the one hard rejection under leniency (see **Decisions taken** §3). Engine API secrets must never ride in a portable blob (spec §4).
- **Documents are applied leniently.** Every key whose value this instance can accept is applied; every key it cannot accept is skipped and reported in the response's `skipped` list. There is no "unknown key" error. (See **Decisions taken** §3.)
- **Signing reuses upstream helpers verbatim**: `searx.webutils.new_hmac(secret_key, payload_bytes)` → hex digest; `searx.webutils.is_hmac_of(secret_key, payload_bytes, signature)` for verification. The hex signature is 64 characters.
- **Token envelope** (spec §5): `token = payload [ "." signature ]`, where `payload = base64url(zlib(json({"epoch": "<hex16>", "doc": {...}})))`. The signature covers the base64url payload **string** (UTF-8 bytes), not the raw JSON. `base64.urlsafe_b64encode` / `urlsafe_b64decode`, padding retained (as upstream's own blob does). The base64url alphabet (`A-Za-z0-9-_`) contains no `.`, so the token contains exactly one `.` — splitting on the first one is unambiguous (verified).
- **Epoch** (spec §5): `sha256(canonical_json({"engines": {...}, "plugins": {...}, "locks": [...]})).hexdigest()[:16]`, where `canonical_json` is `json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")`; `engines` maps each `"<engine>__<category>"` pair the **applied** document names to its instance default state (bool), `plugins` maps each named plugin id to its default state (bool), `locks` is the sorted list of `prefs.cfg.lock`. The engine/category roster and the `key_value_settings` defaults are deliberately excluded (spec §5). A pair or plugin that no longer exists on the instance contributes `null` rather than raising `KeyError`, so the recomputed epoch differs and the token is stale (verified: `null` vs `false` changes the hash).
- **Error contract** (spec §5): malformed token → `400 preferences_invalid`; signature present but wrong → `403 preferences_invalid_signature`; valid signature, stale epoch → `409 preferences_stale`; a document containing `tokens` → `400 preferences_tokens_not_allowed`. These four govern **`GET` only** — `POST` consumes no token and applies its body leniently. The repository has no machine-readable error-code convention today (`ErrorResponse` is `{detail: str}`), so the code is carried as a `detail` prefix: `detail = "<code>: <human message>"` (e.g. `"preferences_stale: the preference token is older than the instance defaults"`). Clients match on the prefix.
- **The POST body must be a JSON object.** A JSON array, string, or `null` is a malformed *document*, not a malformed request: it returns `400 preferences_invalid`, not FastAPI's 422. Implement this by typing the body as `Any` and rejecting non-`dict` values in the handler (see Task 5). A body that is not valid JSON at all still yields FastAPI's 422 — that is a request-syntax error, and the spec's 400 applies to the *document*, not to broken JSON.
- **No silent fallback.** A token that cannot be verified is an error, never a quiet reset to instance defaults (spec §5). A *value* this instance cannot apply is skipped — but never silently: the skip is reported in the response's `skipped` list (see **Decisions taken** §3).
- **Client-held state only.** The server stores nothing per client; the document travels in the token (cookie `sxng_preferences`, header `X-SearXNG-Preferences`, query param `preferences`), precedence **query param > header > cookie** (spec §5).
- **Cookie**: name `sxng_preferences`; `Path=/`; `Max-Age` = upstream `searx.preferences.COOKIE_MAX_AGE` (157680000 = 5 years); `HttpOnly`; `SameSite=Lax`; `Secure` when the request is HTTPS.
- **Test runner is nose2**, not pytest. Run from the repo root. Always prefix repo commands with `env -u PYTHONPATH .venv/bin/python` (the agent runtime's `PYTHONPATH` shadows the pinned FastAPI/Pydantic/Starlette).
- **Lint gates cover `backend/` and `tests/`**: `black --check --target-version py311 --line-length 120 --skip-string-normalization` and `pylint --rcfile .pylintrc`. `.pylintrc`'s `function-rgx` is `(([a-z][a-zA-Z0-9_]{2,30})|(_[a-z0-9_]*))$` — a name must start with a lowercase letter (or an underscore) and be **at most 31 characters**; uppercase is permitted after the first character, so CamelCase is not forbidden by the regex. Every function and test name in this plan satisfies it (the longest, `test_post_empty_document_resets`, is exactly 31 characters). New modules need a module docstring and new classes need a class docstring (`missing-module-docstring` / `missing-class-docstring` are *not* in the pylint disable list). **`unused-import` (W0611) and `invalid-name` (C0103) are also not disabled** — an import the module never uses, or a name over 31 characters, fails the gate (both verified by running pylint against the repo's own `.pylintrc`).

## Review Focus

Six input classes the spec implies but that no task's happy-path test exercises — each is pinned by a test in the task that owns the code.

1. **A document with `enabled_engines` but no `disabled_engines`.** Upstream `parse_dict()` dispatches the engine branch on the *presence of the `disabled_engines` key*, so an enabled-only document is silently dropped by upstream. The normalized dict must always carry both keys. (Task 2)
2. **A document with an out-of-range boolean value** (e.g. `image_proxy: "yes"`). Upstream `BooleanSetting.parse()` does `MAP_STR2BOOL[data]` and raises **`KeyError`**, not `ValidationException` — unhandled, this is a 500. Under leniency it must be **skipped** (not applied, not a 500). (Task 2)
3. **A document with an unknown language code.** Upstream `SearchLanguageSetting.parse()` silently coerces an unknown code to the instance default — a silent fallback the spec forbids. Under leniency the key must be **skipped** rather than coerced. (Task 2)
4. **A token that is well-formed but whose signature does not verify**, and **a token whose embedded epoch no longer matches the instance defaults.** These are two distinct failures (`403` vs `409`) and must not collapse into one. (Task 3)
5. **A document naming an engine pair, plugin id, or category this instance does not have.** It must be skipped and **reported** — the portable-template case that motivates leniency. (Task 2)
6. **A name the catalogue advertised but the document rejects.** The response must publish exactly the expressible engine pairs and the installed plugin ids — no more, no less — or the client writes a document the server refuses. (Task 4)
7. **A key that is silently skipped.** The `POST` response must say so. Leniency that reports nothing is the `.time_range` bug the spec warns about, just relocated. (Task 5)
8. **A token that cannot be decoded at all** (bad base64 padding, not zlib, not JSON). It must be a `400`, not an unhandled `500` from `binascii.Error` / `zlib.error` / `json.JSONDecodeError`. (Task 3)
9. **A token naming an engine the instance no longer has.** It must be reported stale (`409`), not crash with a `KeyError` and not be silently accepted. (Task 3)

Additional trap for the implementer: **the unit-test registry has 2 engine pairs (`dummy engine__general`, `dummy private engine__general`) and the `categories` setting's choices are `['general', 'none']`**; the live instance has ~350 engines and ~33 categories. No test may assert on a specific engine name, engine count, or category count — derive expected values from the live `Preferences` instance instead.

### Discovery gap the catalogue must close (measured on the live instance)

The client can only write a document it can discover, so the GET response must expose exactly the names a document may legally contain. Two mismatches exist today, both measured on the live instance:

- **Engine pairs.** GET's `categories[]` lists an engine under every category it belongs to — 377 derivable `"<engine>__<category>"` pairs — but upstream's `EnginesSetting` only registers pairs for categories in `categories_as_tabs` plus `DEFAULT_CATEGORY` (`searx/preferences.py:309`), i.e. **260 expressible pairs**. 33 engines (e.g. `500px`, `1x`, `chefkoch`) are expressible only as `"<engine>__other"`. A client that picks a pair from what GET advertises therefore has that pair skipped on apply — a preference silently lost, unless the skip is reported (which it is; see **Decisions taken** §3). The response must expose the **expressible** set (`prefs.engines.choices`), not the raw category membership.
- **Plugins.** `PreferencesResponse` has no `plugins` field, so a client cannot learn which plugin ids are nameable, yet `disabled_plugins` / `enabled_plugins` are document keys and an unknown plugin id is skipped on apply. The instance has 9 plugins here, each with an id, name, description and `preference_section` (`searx.plugins._core.PluginInfo`). GET must publish them.

This is why the plan adds a `plugins` catalogue and an explicit `engine_pairs` list to the response (Task 4).

## File Structure

| File | Responsibility |
|---|---|
| `backend/services/preferences.py` (new) | The shared service: `build_preferences()`, document validation/normalization, token minting/verification, epoch computation, transport reading. Imported by `backend/api/preferences.py` today and by `backend/api/search.py` later (spec §11 item 3). |
| `backend/api/preferences.py` (modify) | HTTP layer only: GET (client-aware) and POST. Route wiring, status codes, cookie handling, response assembly. |
| `backend/models/api.py` (modify) | `PreferencesResponse` gains `client`, `token`, `skipped`, `plugins`, `engine_pairs`. |
| `backend/app.py` (modify) | CORS: allow credentials so the browser keeps the cookie. |
| `backend/api/search.py`, `backend/api/autocomplete.py`, `backend/api/frontend.py` (modify) | Replace their private `_build_preferences()` copies with the shared `build_preferences()` (mechanical; no behavior change). |
| `backend/openapi.py` (modify) | Update the `preferences` tag description to cover the write route. |
| `tests/unit/test_preferences_service.py` (new) | Service-level unit tests (filtering, normalization, epoch, token codec). |
| `tests/unit/test_api_preferences.py` (modify) | Endpoint tests for GET/POST. |
| `tests/regression/test_api_preferences_token.py` (new) | Contract regression: the token envelope and error codes. |

---

### Task 1: Shared preferences service module

The foundation every other task imports. It also removes the four copies of `_build_preferences()` that currently live in the route modules — a centralization must migrate **every** call site or the copies drift (the registry-shadowing bug in `backend/api/search.py` is why the `categories` import is aliased there).

**Files:**
- Create: `backend/services/preferences.py`
- Modify: `backend/api/preferences.py` (replace `_build_preferences` with an import), `backend/api/search.py`, `backend/api/autocomplete.py`, `backend/api/frontend.py` (same replacement)
- Test: `tests/unit/test_api_preferences.py` (existing tests must still pass unchanged)

**Interfaces:**
- Consumes: `searx.preferences.Preferences`, `searx.engines.{categories, engines}`, `searx.plugins.STORAGE`, `searx.settings`
- Produces:
  ```python
  # backend/services/preferences.py
  def build_preferences() -> Preferences:
      """Build a Preferences instance from the instance defaults."""
  ```
  Import as `from backend.services.preferences import build_preferences` in the four route modules.

- [ ] **Step 1: Write the failing test**

  Add to `tests/unit/test_api_preferences.py`:

  ```python
  def test_shared_builder_is_used():
      from backend.services.preferences import build_preferences

      prefs = build_preferences()
      assert prefs is not None
      assert prefs.key_value_settings["categories"].get_value() == ["general"]
  ```

  Note: `categories` is a `MultipleChoiceSetting`; its default is `["general"]` (upstream `searx/preferences.py:418`). Do **not** assert on engine or category counts.

- [ ] **Step 2: Run the test to verify it fails**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit tests.unit.test_api_preferences -v`
  Expected: FAIL — `ModuleNotFoundError: No module named 'backend.services.preferences'`

- [ ] **Step 3: Implement `backend/services/preferences.py`**

  Module docstring plus:

  ```python
  from __future__ import annotations

  from searx import settings
  from searx.engines import categories as engine_categories
  from searx.engines import engines
  from searx.plugins import STORAGE as plugins_storage
  from searx.preferences import Preferences


  def build_preferences() -> Preferences:
      """Build a Preferences instance from the instance defaults.

      Mirrors ``searx.webapp.pre_request``: the same themes, the same
      category list, the same engine and plugin registries, and no
      client-supplied state (the client document is applied separately
      via ``parse_dict``).
      """
      themes = [settings.get("ui", {}).get("default_theme", "simple")]
      return Preferences(
          themes,
          list(engine_categories.keys()),
          engines,  # pyright: ignore[reportArgumentType]
          plugins_storage,
          None,
      )
  ```

  The `categories` import is aliased to `engine_categories` deliberately: in `backend/api/search.py` the module-level name `categories` is shadowed by a query parameter, and the alias keeps the shared builder immune to that (the same trap the skill records).

- [ ] **Step 4: Migrate all four call sites**

  In `backend/api/preferences.py`, `backend/api/search.py`, `backend/api/autocomplete.py`, `backend/api/frontend.py`: delete the private `_build_preferences()` function, add `from backend.services.preferences import build_preferences`, and rename every call site `_build_preferences()` → `build_preferences()`.

  Then prune the imports that the deleted function was the only user of. The list below is **verified by AST analysis of the current sources** — do not guess, and do not prune by eye:

  - `backend/api/preferences.py`: remove `Preferences`, `plugins_storage`, `settings`. **Keep** `engines` and `categories` — the GET handler still uses them (`engines.values()`, `categories`).
  - `backend/api/search.py`: remove `Preferences`, `engines`, `plugins_storage`, `settings`. **Keep** `engine_categories` — the search handler uses it for the bang-query scope and for `get_selected_categories`. **Keep** `from searx.preferences import ValidationException` (still caught around `parse_dict`); change that import to name only `ValidationException`.
  - `backend/api/autocomplete.py`: remove `Preferences`, `categories`, `engines`, `plugins_storage`, `settings` — all five become unused.
  - `backend/api/frontend.py`: remove **nothing**. `Preferences` is still used as the annotation of `_client_settings(prefs: Preferences)`, `plugins_storage` is still used for `plugins_storage.info` / `allowed_plugins`, and `engines` and `categories` are used throughout the template context.

  Verify no copy survives and no import was over-pruned:

  Run: `grep -rn "_build_preferences" backend/`
  Expected: no output.

  Run: `env -u PYTHONPATH .venv/bin/python -c "import backend.app"` — must not raise `NameError`/`ImportError`.

  Run: `env -u PYTHONPATH .venv/bin/python -m pylint --rcfile .pylintrc backend`
  Expected: no `unused-import` (W0611) findings — pylint is the check that catches an over-pruned import.

- [ ] **Step 5: Run the test to verify it passes**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit tests.unit.test_api_preferences -v`
  Expected: PASS (4 tests)

- [ ] **Step 6: Run the full unit suite to verify nothing regressed**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit`
  Expected: OK (baseline: 403 tests, measured on branch `hermes` at HEAD `fa30171`)

- [ ] **Step 7: Commit**

  ```bash
  git add backend/services/preferences.py backend/api/ tests/unit/test_api_preferences.py
  git commit -m "refactor(backend): centralize Preferences construction in a service module"
  ```

---

### Task 2: Document filtering and normalization

Turns an untrusted JSON body into (a) the subset of it this instance can apply, (b) a report of what was skipped, and (c) the exact `dict[str, str]` upstream `parse_dict()` expects.

Under the leniency decision (**Decisions taken** §3) this task **filters** rather than **rejects**: a key this instance cannot apply is skipped and reported, not an error. The only hard rejection is the `tokens` key.

**Files:**
- Modify: `backend/services/preferences.py`
- Test: `tests/unit/test_preferences_service.py` (new)

**Interfaces:**
- Consumes: `build_preferences()` from Task 1; `searx.preferences.ValidationException`; `searx.preferences.MAP_STR2BOOL`
- Produces:
  ```python
  # backend/services/preferences.py

  class PreferencesError(Exception):
      """A client preference error carrying a status code and an error code."""

      def __init__(self, status_code: int, code: str, message: str) -> None:
          self.status_code = status_code
          self.code = code
          self.message = message
          super().__init__(message)

  def allowed_keys(prefs: Preferences) -> list[str]:
      """Return the document keys this instance understands, sorted."""

  def filter_document(
      raw: dict[str, object], prefs: Preferences
  ) -> tuple[dict[str, object], list[str]]:
      """Return (applicable document, human-readable notes for skipped keys)."""

  def normalize_document(doc: dict[str, object], prefs: Preferences) -> dict[str, str]:
      """Convert an applicable document into the string form parse_dict() takes."""
  ```
  `filter_document` raises `PreferencesError(400, "preferences_tokens_not_allowed", ...)` for the `tokens` key and nothing else. Every other inapplicable key is skipped, and a note naming the key and the reason is appended to the returned list.

- [ ] **Step 1: Write the failing tests**

  Create `tests/unit/test_preferences_service.py`:

  ```python
  # SPDX-License-Identifier: AGPL-3.0-or-later
  """Tests for the client preference document service."""

  # pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

  # Importing backend.app runs create_app(), which calls init_kernel().
  # build_preferences() needs the initialized kernel: searx.favicons.proxy.CFG
  # is None until then and constructing Preferences raises AttributeError.
  import backend.app  # noqa: F401  pylint: disable=unused-import

  from backend.services.preferences import (
      PreferencesError,
      allowed_keys,
      build_preferences,
      filter_document,
      normalize_document,
  )
  ```

  The `import backend.app` line is **required**: without it this test module fails when run on its own with `AttributeError: 'NoneType' object has no attribute 'resolver_map'` (verified). `build_preferences()` cannot construct a `Preferences` instance before the kernel is initialized.

  Then append the tests:

  ```python
  def test_unknown_key_skipped():
      prefs = build_preferences()
      doc, skipped = filter_document({"nope": 1, "categories": ["general"]}, prefs)
      assert "nope" not in doc
      assert doc["categories"] == ["general"]
      assert any("nope" in note for note in skipped)


  def test_tokens_key_rejected():
      prefs = build_preferences()
      try:
          filter_document({"tokens": {"engine": "secret"}}, prefs)
      except PreferencesError as exc:
          assert exc.code == "preferences_tokens_not_allowed"
      else:
          raise AssertionError("tokens was accepted")


  def test_bad_bool_skipped():
      prefs = build_preferences()
      doc, skipped = filter_document({"image_proxy": "yes"}, prefs)
      assert "image_proxy" not in doc
      assert any("image_proxy" in note for note in skipped)


  def test_bad_language_skipped():
      prefs = build_preferences()
      doc, skipped = filter_document({"language": "xx-INVALID"}, prefs)
      assert "language" not in doc
      assert any("language" in note for note in skipped)


  def test_unknown_engine_skipped():
      prefs = build_preferences()
      doc, skipped = filter_document({"disabled_engines": ["no such engine__general"]}, prefs)
      assert "disabled_engines" not in doc
      assert any("disabled_engines" in note for note in skipped)


  def test_unknown_plugin_skipped():
      prefs = build_preferences()
      doc, skipped = filter_document({"disabled_plugins": ["no_such_plugin"]}, prefs)
      assert "disabled_plugins" not in doc
      assert any("disabled_plugins" in note for note in skipped)


  def test_unknown_default_category():
      prefs = build_preferences()
      doc, skipped = filter_document({"default_category": "no such category"}, prefs)
      assert "default_category" not in doc
      assert any("default_category" in note for note in skipped)


  def test_known_default_category():
      prefs = build_preferences()
      doc, skipped = filter_document({"default_category": "general"}, prefs)
      assert doc["default_category"] == "general"
      assert skipped == []


  def test_enabled_only_applies():
      prefs = build_preferences()
      # Take a real pair from the live registry -- the unit-test registry
      # has only the two dummy engines, so a hardcoded name would be skipped.
      pair = sorted(prefs.engines.choices)[0]
      doc, _ = filter_document({"enabled_engines": [pair]}, prefs)
      normalized = normalize_document(doc, prefs)
      # Upstream dispatches the engine branch on the presence of the
      # disabled_engines key, so both keys must always be present.
      assert normalized["disabled_engines"] == ""
      assert normalized["enabled_engines"] == pair


  def test_boolean_normalization():
      """The boolean string forms must normalize by lookup, not truthiness."""
      prefs = build_preferences()
      doc, _ = filter_document({"image_proxy": "none"}, prefs)
      # "none" is in MAP_STR2BOOL and maps to False -> "0", not "1".
      assert normalize_document(doc, prefs)["image_proxy"] == "0"
      doc, _ = filter_document({"image_proxy": "on"}, prefs)
      assert normalize_document(doc, prefs)["image_proxy"] == "1"
      doc, _ = filter_document({"image_proxy": True}, prefs)
      assert normalize_document(doc, prefs)["image_proxy"] == "1"
      doc, _ = filter_document({"image_proxy": False}, prefs)
      assert normalize_document(doc, prefs)["image_proxy"] == "0"
  ```

  The `test_enabled_only_applies` case is the regression for Review Focus #1: upstream's `parse_dict()` dispatches the engine branch on the **presence of the `disabled_engines` key**, so an enabled-only document is silently dropped unless the normalizer always emits both keys.

  Note the difference between "skipped" and "rejected": `test_unknown_key_skipped` asserts the *other* key in the same document still applies, which is what makes a portable template work.

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit tests.unit.test_preferences_service -v`
  Expected: FAIL — `ImportError: cannot import name 'filter_document'`

- [ ] **Step 3: Implement filtering and normalization**

  Add to `backend/services/preferences.py`:

  ```python
  from searx.preferences import (
      MAP_STR2BOOL,
      BooleanSetting,
      EnumStringSetting,
      MapSetting,
      MultipleChoiceSetting,
  )

  #: Fork-only document key: the default search category. Not an upstream
  #: ``key_value_settings`` entry -- it is resolved in the backend.
  DEFAULT_CATEGORY_KEY = "default_category"

  #: The upstream keys that carry engine and plugin deltas rather than a
  #: single absolute value.
  ENGINE_KEYS = ("disabled_engines", "enabled_engines")
  PLUGIN_KEYS = ("disabled_plugins", "enabled_plugins")
  ```

  Import exactly these six names. `ValidationException` is **not** imported into the service module: the service never calls `parse_dict()`, so it would be an unused import, and the repo's pylint config does not disable `unused-import` (W0611) — importing it fails the lint gate. `ValidationException` is caught in the **route** module (Tasks 4 and 5), which is where `parse_dict()` is called. Likewise `Any` is not needed here: the service's signatures use `dict[str, object]` and `dict[str, str]`, not `Any`.

  `allowed_keys(prefs)` returns `sorted(set(prefs.key_value_settings) | set(ENGINE_KEYS) | set(PLUGIN_KEYS) | {DEFAULT_CATEGORY_KEY})`.

  `filter_document(raw, prefs)` returns `(applicable, skipped)`:

  1. `tokens` in `raw` → raise `PreferencesError(400, "preferences_tokens_not_allowed", "engine tokens are not allowed in a preference document")`. This is the only hard rejection.
  2. For each key of `raw`, decide whether this instance can apply it. A key that cannot be applied is **skipped** — omitted from `applicable`, with a note appended to `skipped` naming the key and the reason. A key that can be applied is kept, with its value in JSON-native form.

     The per-key rules (each rule answers "can this instance apply this value?"):

     - **A key not in `allowed_keys(prefs)`** → skipped. It is either a typo or a key that belongs to a different instance; under leniency the two are indistinguishable, which is why the skip is reported.
     - `MultipleChoiceSetting` (i.e. `categories`): value must be a `list[str]`; every element must be in `setting.choices` (upstream's own membership test). Upstream's choices are the instance's categories **plus the `"none"` sentinel**, so `["none"]` is accepted here — `categories` is upstream's own key and its sentinel is part of its contract. A value of the wrong shape, or a list with any element outside `choices`, is skipped wholesale (do not silently drop individual elements — a partially applied `categories` list would mean something different from what the client asked for). (`default_category` is a fork-only key and gets no sentinel — see below.)
     - `BooleanSetting` (`image_proxy`, `results_on_new_tab`, `center_alignment`, `query_in_title`, `search_on_category_select`): value must be a JSON `bool`, or one of the seven strings in `MAP_STR2BOOL`; anything else (including `"yes"`, `1`, `0`) is skipped. This is what keeps upstream's `MAP_STR2BOOL[data]` `KeyError` from ever being reached.
     - `MapSetting` (`safesearch`): value must be a JSON `int` in `setting.map.values()` (`0`, `1`, `2`) **or** one of `setting.map`'s string keys (`"0"`, `"1"`, `"2"`); anything else is skipped. **Reject `bool` explicitly** — `True`/`False` are `int` instances in Python, so a bare `isinstance(value, int)` check lets `true` through and the "matching key" lookup then finds nothing. Test `isinstance(value, bool)` *before* `isinstance(value, int)`.
     - `EnumStringSetting` (`language`, `locale`, `autocomplete`, `favicon_resolver`, `method`, `theme`, `doi_resolver`, `simple_style`, `hotkeys`, `url_formatting`): value must be a `str` **and** a member of `setting.choices`. The membership check is deliberate: upstream `SearchLanguageSetting.parse()` silently coerces an unknown language code to the instance default, which the spec forbids ("no silent fallback") and which leniency would otherwise turn into a silent wrong value. `SearchLanguageSetting` is a subclass of `EnumStringSetting`, so it is covered by the same branch.
     - `ENGINE_KEYS` / `PLUGIN_KEYS`: value must be a `list[str]`; every engine entry must be a key of `prefs.engines.choices` (the pairs this instance can express) and every plugin entry must be a key of `prefs.plugins.choices`. If **any** entry is unknown, the whole key is skipped — the same "all or nothing" rule as `categories`, for the same reason. This is the portable-template case: a document naming an engine this instance does not have is applied to the rest of the document and the engine is reported as skipped.
     - `DEFAULT_CATEGORY_KEY`: value must be a `str` in `searx.engines.categories` keys — the **real** categories (33 on this instance: `general`, `videos`, `social media`, …), **not** the `"none"` sentinel that the `categories` *setting* also accepts. `searx.engines.categories` has no `"none"` entry (verified), so the fork-only key and the upstream key use different choice sets. Otherwise skipped.
  3. Return `(applicable, skipped)`. `applicable` contains only the keys that were present *and* applicable, with values in JSON-native form.

  `normalize_document(doc, prefs)` returns the `dict[str, str]` for `parse_dict()`:

  - `categories` → `",".join(value)`
  - `ENGINE_KEYS` / `PLUGIN_KEYS` → `",".join(value)` for **both** members of each pair, always emitting `disabled_engines`, `enabled_engines`, `disabled_plugins`, `enabled_plugins` keys even when the document omitted them (empty string when absent). Upstream dispatches the engine branch on the presence of the `disabled_engines` key, so an enabled-only document would otherwise be dropped (Review Focus #1).
  - `BooleanSetting` → the **value** of the matching `MAP_STR2BOOL` entry, rendered as `"1"` for `True` and `"0"` for `False`. Look the string up in `MAP_STR2BOOL`; do **not** test truthiness. `"none"` maps to `False` and must normalize to `"0"`, not `"1"`. A JSON `True`/`False` maps to `"1"`/`"0"` directly.
  - `MapSetting` (`safesearch`) → the matching key of `setting.map` (`"0"`, `"1"`, `"2"`)
  - everything else → the string value unchanged.

  Because the normalizer always emits all four engine/plugin keys, `normalize_document` must be called on the **applicable** document — the skipped keys must not reach it.

- [ ] **Step 4: Run the tests to verify they pass**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit tests.unit.test_preferences_service -v`
  Expected: PASS

- [ ] **Step 5: Run the full unit suite**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit`
  Expected: OK

- [ ] **Step 6: Commit**

  ```bash
  git add backend/services/preferences.py tests/unit/test_preferences_service.py
  git commit -m "feat(backend): validate and normalize client preference documents"
  ```

---

### Task 3: Token minting, verification, and epoch computation

**Files:**
- Modify: `backend/services/preferences.py`
- Test: `tests/unit/test_preferences_service.py`

**Interfaces:**
- Consumes: `build_preferences()`, `filter_document()`, `normalize_document()` from Tasks 1–2; `searx.webutils.new_hmac`, `searx.webutils.is_hmac_of`; `searx.settings["server"]["secret_key"]`
- Produces:
  ```python
  # backend/services/preferences.py

  def compute_epoch(doc: dict[str, object], prefs: Preferences) -> str:
      """Return the 16-hex-char epoch of a document against the instance defaults."""

  def mint_token(doc: dict[str, object], prefs: Preferences) -> str:
      """Mint a signed, epoch-stamped token carrying a document."""

  def decode_token(token: str) -> tuple[dict[str, object], str, bool]:
      """Split a token into (document, epoch, signed)."""

  def verify_token(token: str, prefs: Preferences) -> tuple[dict[str, object], list[str]]:
      """Verify a token and return (filtered document, skipped notes)."""

  def sign_payload(payload: str) -> str:
      """Return the hex HMAC of a base64url payload, using the instance secret key."""

  def _secret_key() -> str:
      """Return the instance secret key used to sign preference tokens."""

  def _canonical_json(obj: object) -> bytes:
      """Serialize an object to canonical (sorted, compact) JSON bytes."""

  def _named_pairs(doc: dict[str, object]) -> set[str]:
      """Return the engine pairs the document names, across disabled and enabled."""

  def _named_plugins(doc: dict[str, object]) -> set[str]:
      """Return the plugin ids the document names, across disabled and enabled."""
  ```
  `verify_token` raises `PreferencesError(400, "preferences_invalid", ...)` for a malformed token, `PreferencesError(403, "preferences_invalid_signature", ...)` for a signature that does not verify, and `PreferencesError(409, "preferences_stale", ...)` when a signed token's embedded epoch no longer matches the instance defaults.

- [ ] **Step 1: Write the failing tests**

  First extend the import block in `tests/unit/test_preferences_service.py` (Task 2 created it) to add the Task 3 names:

  ```python
  from backend.services.preferences import (
      PreferencesError,
      allowed_keys,
      build_preferences,
      compute_epoch,
      filter_document,
      mint_token,
      normalize_document,
      sign_payload,
      verify_token,
  )
  ```

  Then append the tests:

  ```python
  def test_token_roundtrip():
      prefs = build_preferences()
      doc, _ = filter_document({"categories": ["general"]}, prefs)
      token = mint_token(doc, prefs)
      verified, skipped = verify_token(token, prefs)
      assert verified["categories"] == ["general"]
      assert skipped == []


  def test_bad_signature_rejected():
      prefs = build_preferences()
      doc, _ = filter_document({"categories": ["general"]}, prefs)
      token = mint_token(doc, prefs)
      payload, _, _ = token.partition(".")
      try:
          verify_token(payload + "." + ("0" * 64), prefs)
      except PreferencesError as exc:
          assert exc.status_code == 403
          assert exc.code == "preferences_invalid_signature"
      else:
          raise AssertionError("bad signature was accepted")


  def test_malformed_token_rejected():
      prefs = build_preferences()
      try:
          verify_token("not-a-token", prefs)
      except PreferencesError as exc:
          assert exc.status_code == 400
          assert exc.code == "preferences_invalid"
      else:
          raise AssertionError("malformed token was accepted")


  def test_undecodable_token_rejected():
      """A token that fails to decode is a 400, not an uncaught exception."""
      prefs = build_preferences()
      # "abc" is not valid padded base64 -> binascii.Error if uncaught.
      # "eyJ9" is valid base64 but not zlib -> zlib.error.
      # "eJzLyy9RyCrOzwMADiYDLA==" is valid zlib but not JSON -> JSONDecodeError.
      # "a.b" -> binascii.Error; "!!!" -> decodes to empty -> JSONDecodeError.
      for bad in ("abc", "eyJ9", "eJzLyy9RyCrOzwMADiYDLA==", "a.b", "!!!"):
          try:
              verify_token(bad, prefs)
          except PreferencesError as exc:
              assert exc.status_code == 400, f"{bad!r} gave {exc.status_code}"
              assert exc.code == "preferences_invalid", f"{bad!r} gave {exc.code}"
          else:
              raise AssertionError(f"malformed token {bad!r} was accepted")


  def test_stale_token_rejected():
      prefs = build_preferences()
      doc, _ = filter_document({"categories": ["general"]}, prefs)
      token = mint_token(doc, prefs)
      stale = _restamp(token, _other_epoch())
      try:
          verify_token(stale, prefs)
      except PreferencesError as exc:
          assert exc.status_code == 409
          assert exc.code == "preferences_stale"
      else:
          raise AssertionError("stale token was accepted")


  def test_epoch_tracks_default():
      prefs = build_preferences()
      doc, _ = filter_document({"categories": ["general"]}, prefs)
      first = compute_epoch(doc, prefs)
      assert first == compute_epoch(doc, prefs)
      assert len(first) == 16


  def test_vanished_pair_is_stale():
      """A named pair the instance no longer has makes the token stale."""
      prefs = build_preferences()
      pair = sorted(prefs.engines.choices)[0]
      doc, _ = filter_document({"disabled_engines": [pair]}, prefs)
      token = mint_token(doc, prefs)
      # Simulate the engine being removed after the token was minted:
      # the pair's default state becomes unavailable (None).
      vanished = dict(prefs.engines.default_choices)
      vanished.pop(pair, None)
      try:
          prefs.engines.default_choices = vanished
          verify_token(token, prefs)
      except PreferencesError as exc:
          assert exc.status_code == 409
          assert exc.code == "preferences_stale"
      else:
          raise AssertionError("a token naming a vanished engine was accepted")


  def test_dropped_setting_is_skipped():
      """A key_value_settings value the instance dropped is skipped, not stale.

      The epoch covers engine pairs, plugins and locks -- not the
      key_value_settings defaults -- so losing a setting value does not
      invalidate the token. The key is simply no longer applicable, and
      verify_token must report it so the client learns its preference
      stopped applying.
      """
      prefs = build_preferences()
      theme = prefs.key_value_settings["theme"].get_value()
      doc, _ = filter_document({"theme": theme}, prefs)
      token = mint_token(doc, prefs)
      # Remove the theme value from the instance's choices.
      prefs.key_value_settings["theme"].choices = [
          c for c in prefs.key_value_settings["theme"].choices if c != theme
      ]
      verified, skipped = verify_token(token, prefs)
      assert verified == {}
      assert any("theme" in note for note in skipped)
  ```

  `test_vanished_pair_is_stale` is the regression for the `.get()` in `compute_epoch`: the pair contributes `null` to the epoch payload, so the recomputed epoch differs and the token is rejected as stale rather than raising `KeyError` (verified: `null` vs `false` changes the digest). `default_choices` is a plain instance attribute (`searx/preferences.py:255`), so the test may replace it directly; because `build_preferences()` constructs a fresh `Preferences` per call, the mutation does not leak into other tests.

  Both helpers live in `tests/unit/test_preferences_service.py`, above the tests that use them:

  ```python
  def _other_epoch() -> str:
      """Return a 16-char hex epoch that cannot collide with a real one."""
      return "0" * 16


  def _restamp(token: str, epoch: str) -> str:
      """Re-encode a token with a different epoch and re-sign it."""
      import base64
      import json
      import zlib

      payload, _, _ = token.partition(".")
      body = json.loads(zlib.decompress(base64.urlsafe_b64decode(payload)))
      body["epoch"] = epoch
      fresh = base64.urlsafe_b64encode(
          zlib.compress(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
      ).decode("ascii")
      return fresh + "." + sign_payload(fresh)
  ```

  The real epoch is a hex digest of a hash, so the all-zero epoch cannot collide with it. `sign_payload` comes from the extended import block above.

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit tests.unit.test_preferences_service -v`
  Expected: FAIL — `ImportError: cannot import name 'mint_token'`

- [ ] **Step 3: Implement the token codec and epoch**

  Add to `backend/services/preferences.py`:

  ```python
  import base64
  import binascii
  import hashlib
  import json
  import zlib

  from searx.webutils import is_hmac_of, new_hmac

  #: Separator between the payload and the hex signature.
  TOKEN_SEPARATOR = "."

  #: Maximum size of the decompressed token payload (bytes).
  MAX_PAYLOAD_BYTES = 65536


  def _secret_key() -> str:
      return settings["server"]["secret_key"]


  def _canonical_json(obj: object) -> bytes:
      return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
  ```

  `compute_epoch(doc, prefs)` — `doc` is the **applicable** document (the filtered one), not the submitted one:

  ```python
  payload = {
      "engines": {
          pair: prefs.engines.default_choices.get(pair)
          for pair in sorted(_named_pairs(doc))
      },
      "plugins": {
          plugin: prefs.plugins.default_choices.get(plugin)
          for plugin in sorted(_named_plugins(doc))
      },
      "locks": sorted(prefs.cfg.lock),
  }
  return hashlib.sha256(_canonical_json(payload)).hexdigest()[:16]
  ```

  Use `.get(...)`, **not** `[...]`: a pair or plugin the document names but the instance no longer has yields `None` instead of raising `KeyError`. That `null` changes the canonical JSON and therefore the hash, so a token minted before an engine was removed is detected as **stale (409)** rather than crashing the request (verified: `null` vs `false` produces a different digest). This extends the spec's §5 rule — "changing the default state of an engine ... invalidates" — to the engine disappearing outright.

  `_named_pairs(doc)` returns the union of the document's `disabled_engines` and `enabled_engines` entries; `_named_plugins(doc)` the union of `disabled_plugins` and `enabled_plugins`.

  `mint_token(doc, prefs)`:

  ```python
  payload = base64.urlsafe_b64encode(
      zlib.compress(_canonical_json({"epoch": compute_epoch(doc, prefs), "doc": doc}))
  ).decode("ascii")
  return payload + TOKEN_SEPARATOR + sign_payload(payload)
  ```

  `sign_payload(payload)` returns `new_hmac(_secret_key(), payload.encode("utf-8"))` — the hex digest, 64 chars. (This is the only signing entry point: the module has no separate `_sign`, and the test helper `_restamp` calls `sign_payload` too.)

  `decode_token(token)` — takes **only** the token; it performs no validation and needs no `Preferences`:

  - Split on the **first** `.`. Base64url output contains no `.`, and the hex signature contains no `.`, so the split is unambiguous.
  - No separator → the token is unsigned. Still decode the payload (an unsigned token is accepted per spec §5, but it carries no verifiable epoch, so the epoch check is skipped).
  - Base64 decode (`base64.urlsafe_b64decode`) → `zlib.decompressobj()` with `MAX_PAYLOAD_BYTES` as the `max_length`; if `unconsumed_tail` is non-empty after decompressing, the payload is larger than the limit → `PreferencesError(400, "preferences_invalid", ...)`. Check `unconsumed_tail` only — a payload whose decompressed size is exactly `MAX_PAYLOAD_BYTES` is legal and must not be rejected.
  - `json.loads` → must be a dict with a `doc` dict and a 16-hex-char `epoch` string; otherwise `PreferencesError(400, "preferences_invalid", ...)`.
  - Return `(doc, epoch, signed)`.

  **Every decode failure must become a `PreferencesError`, never propagate.** An untrusted token string can fail in several ways, and each raises a different built-in exception: `base64.urlsafe_b64decode` raises `binascii.Error` (e.g. `"abc"` has incorrect padding — verified), `zlib.decompressobj().decompress` raises `zlib.error`, and `json.loads` raises `json.JSONDecodeError`. Wrap the whole decode in `try/except (binascii.Error, zlib.error, json.JSONDecodeError, UnicodeDecodeError, ValueError)` and re-raise as `PreferencesError(400, "preferences_invalid", "the preference token is malformed")`. Without this, a malformed token produces a **500**, not the `400` the spec's error table requires (verified — `binascii.Error: Incorrect padding` escapes `decode_token` uncaught).

  `verify_token(token, prefs)`:

  - Re-split the token itself: `payload, separator, signature = token.partition(TOKEN_SEPARATOR)`. (`decode_token` returns the document, not the raw parts, so `verify_token` re-partitions rather than relying on names that are not in scope.)
  - `(doc, epoch, signed) = decode_token(token)`.
  - If signed: verify with `is_hmac_of(_secret_key(), payload.encode("utf-8"), signature)`; on failure raise `PreferencesError(403, "preferences_invalid_signature", "the preference token signature does not verify")`.
  - Re-filter the decoded document with `filter_document(doc, prefs)`, which returns `(filtered, skipped)`. A token minted on another instance may name pairs or plugins this instance does not have; under leniency those are skipped, not rejected. A token whose document contains `tokens` is rejected outright (`preferences_tokens_not_allowed`).
  - If signed: recompute `compute_epoch(filtered, prefs)` and compare with the embedded epoch; on mismatch raise `PreferencesError(409, "preferences_stale", "the preference token is stale; re-fetch the preferences and post a new document")`.
  - Return `(filtered, skipped)`.

  Unsigned tokens skip the epoch comparison only — their document is still filtered (spec §5: unsigned tokens are accepted, but they carry no verifiable epoch).

  **Error precedence** (a stale token whose document contains `tokens` reports 400, not 409): signature → document filtering → epoch. The spec does not mandate an order; this one is stated so the behavior is a decision rather than an accident.

- [ ] **Step 4: Run the tests to verify they pass**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit tests.unit.test_preferences_service -v`
  Expected: PASS

- [ ] **Step 5: Run the full unit suite**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit`
  Expected: OK

- [ ] **Step 6: Commit**

  ```bash
  git add backend/services/preferences.py tests/unit/test_preferences_service.py
  git commit -m "feat(backend): mint and verify signed client preference tokens"
  ```

---

### Task 4: Response model and client-aware GET

**Files:**
- Modify: `backend/models/api.py`, `backend/api/preferences.py`
- Test: `tests/unit/test_api_preferences.py`

**Interfaces:**
- Consumes: `build_preferences()`, `verify_token()`, `normalize_document()` from Tasks 1–3; `searx.preferences.ValidationException`
- Produces:
  ```python
  # backend/models/api.py

  class PluginMeta(BaseModel):
      """An installed plugin, as offered to clients in the preference catalogue."""

      id: str
      name: str = ""
      description: str = ""
      preference_section: str = ""

  class PreferencesResponse(BaseModel):
      """The preference catalogue: selectable options, defaults, current values and locked keys."""

      categories: list[CategoryMeta] = Field(default_factory=list)
      engines: list[EngineMeta] = Field(default_factory=list)
      plugins: list[PluginMeta] = Field(default_factory=list)
      engine_pairs: list[str] = Field(default_factory=list)
      locales: list[LocaleMeta] = Field(default_factory=list)
      formats: list[str] = Field(default_factory=list)
      styles: list[str] = Field(default_factory=list)
      default: dict[str, Any] = Field(default_factory=dict)
      current: dict[str, Any] = Field(default_factory=dict)
      client: dict[str, Any] = Field(default_factory=dict)
      skipped: list[str] = Field(default_factory=list)
      locked: list[str] = Field(default_factory=list)
      token: str = ""
  ```
  and in `backend/services/preferences.py`:
  ```python
  from fastapi import Request


  def read_client_token(request: Request) -> str | None:
      """Read the client preference token from the request transports."""
  ```
  The service module needs its **own** `from fastapi import Request` import — `read_client_token` lives here, so `Request` must be in scope here or pylint reports `undefined-variable` (E0602) on the annotation. This is separate from the route module's own `Request` import.
  Precedence: query param `preferences` > header `X-SearXNG-Preferences` > cookie `sxng_preferences` (spec §5). It lives in the **service**, not the route module: the search route (spec §11 item 3) must read the same transports, and a route-local copy would drift.

  **`skipped`** lists the document keys this instance could not apply, with a reason for each. `POST` populates it from the filter it ran on the submitted document. `GET` populates it from the filter `verify_token` ran on the token's document — and it is **not** always empty there. Two distinct cases:

  - A named **engine pair or plugin** the instance no longer has → the recomputed epoch differs (the epoch covers engine and plugin defaults), so `GET` answers **409 stale** and never reaches the response body. The client's state no longer matches the instance; that is more important than a per-key skip report.
  - A named **`key_value_settings` value** (e.g. `theme: "simple"`) that the instance no longer accepts → the epoch is **unchanged**, because the epoch deliberately covers only engine pairs, plugins and locks (spec §5). So `GET` answers **200** with the key dropped from `client` and listed in `skipped`. The client's preference silently stopped applying — which is exactly why the skip is reported rather than dropped.

  Both cases are exercised by tests: the stale case in Task 3 (`test_vanished_pair_is_stale`), the skip case in Task 4.

  **`engine_pairs`** is the list of `"<engine>__<category>"` pairs a document may legally name — exactly the keys of `prefs.engines.choices`. It is the expressible set, **not** the raw per-category engine membership that `categories[]` implies (see **Discovery gap** above: 377 vs 260 pairs on the live instance). **`plugins`** lists the installed plugins, so `disabled_plugins` / `enabled_plugins` are discoverable.

- [ ] **Step 1: Write the failing tests**

  First add the imports the new tests need to `tests/unit/test_api_preferences.py` (the file currently imports only `TestClient` and `backend.app`):

  ```python
  from backend.services.preferences import (
      build_preferences,
      filter_document,
      mint_token,
  )
  ```

  Then append:

  ```python
  def test_get_reports_client():
      res = client.get("/api/v1/preferences")
      assert res.status_code == 200
      data = res.json()
      assert data["client"] == {}
      assert data["token"] == ""
      assert data["current"] == data["default"]


  def test_advertised_names_valid():
      """Everything the catalogue advertises must be acceptable in a document."""
      res = client.get("/api/v1/preferences")
      data = res.json()
      prefs = build_preferences()

      # Every advertised engine pair is a legal document value.
      assert data["engine_pairs"]
      for pair in data["engine_pairs"]:
          applicable, skipped = filter_document({"disabled_engines": [pair]}, prefs)
          assert applicable["disabled_engines"] == [pair]
          assert skipped == []

      # Every advertised plugin id is a legal document value.
      assert data["plugins"]
      for plugin in data["plugins"]:
          applicable, skipped = filter_document({"disabled_plugins": [plugin["id"]]}, prefs)
          assert applicable["disabled_plugins"] == [plugin["id"]]
          assert skipped == []

      # And the advertised sets are exactly the expressible ones.
      assert sorted(data["engine_pairs"]) == sorted(prefs.engines.choices)
      assert sorted(p["id"] for p in data["plugins"]) == sorted(prefs.plugins.choices)


  def test_get_applies_client_doc():
      prefs = build_preferences()
      doc, _ = filter_document({"categories": ["general"]}, prefs)
      token = mint_token(doc, prefs)
      res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": token})
      assert res.status_code == 200
      data = res.json()
      assert data["client"]["categories"] == ["general"]
      assert data["token"] == token
  ```

  `test_advertised_names_valid` is the invariant that matters most under leniency: the catalogue must advertise exactly the names the filter accepts, and the filter must accept them **without skipping** (hence the `skipped == []` assertions). If the catalogue advertised a name the filter skipped, clients would write documents that silently lose preferences.

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit tests.unit.test_api_preferences -v`
  Expected: FAIL — `KeyError: 'client'`

- [ ] **Step 3: Add the response model fields**

  In `backend/models/api.py`, add the `PluginMeta` class above `PreferencesResponse`:

  ```python
  class PluginMeta(BaseModel):
      """An installed plugin, as offered to clients in the preference catalogue."""

      id: str
      name: str = ""
      description: str = ""
      preference_section: str | None = "general"
  ```

  (`preference_section` is `str | None` upstream — `searx/plugins/_core.py:45` types it `Literal["general", "ui", "privacy", "query"] | None`. Keep it nullable so the model does not reject a plugin with no section.)

  Then add to `PreferencesResponse`:

  ```python
      plugins: list[PluginMeta] = Field(default_factory=list)
      engine_pairs: list[str] = Field(default_factory=list)
      client: dict[str, Any] = Field(default_factory=dict)
      skipped: list[str] = Field(default_factory=list)
      token: str = ""
  ```

  Place `client` next to `current`, `skipped` after `client`, and `token` last. Update the class docstring to mention the client document, the keys this instance skipped, the token, the installed plugins and the expressible engine pairs.

- [ ] **Step 4: Make GET client-aware**

  In `backend/api/preferences.py`:

  - Import `Request` from `fastapi` and add `from backend.services.preferences import build_preferences, read_client_token, verify_token` plus `from searx.preferences import ValidationException`. (Task 1 already removed the now-unused `Preferences` import from this module — do not re-add it; the handler does not annotate with it.)
  - Implement `read_client_token(request)` **in `backend/services/preferences.py`** (Task 4's Interfaces block places it there). It returns the first non-empty value of: `request.query_params.get("preferences")`, `request.headers.get("X-SearXNG-Preferences")`, `request.cookies.get("sxng_preferences")`.
  - Change the route signature to `def get_preferences(request: Request) -> PreferencesResponse:`.
  - Inside the handler:
    1. Build `prefs = build_preferences()`.
    2. Snapshot `default = {name: setting.get_value() for name, setting in prefs.key_value_settings.items()}` **before** applying any client document.
    3. Read the token; if present, `doc = verify_token(token, prefs)` — catch `PreferencesError` and re-raise as `HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}")`. `verify_token` returns the **filtered** document and, alongside it, the notes for the keys it skipped.
    4. If a document was found, apply it: `prefs.parse_dict(normalize_document(doc, prefs))` inside `try/except ValidationException` → `HTTPException(400, detail=f"preferences_invalid: {exc}")`.
    5. Snapshot `current = {name: setting.get_value() for name, setting in prefs.key_value_settings.items()}` **after** applying the document.
    6. Populate the catalogue:
       - `engine_pairs=sorted(prefs.engines.choices)` — the expressible pairs, so every pair the server advertises is one a document may name.
       - `plugins=[PluginMeta(id=i.id, name=i.name, description=i.description, preference_section=i.preference_section) for i in plugins_storage.info]` — build from `plugins_storage.info`, **not** from iterating the plugins: a `Plugin` object carries only `id` (plus `active`/`keywords`); the name, description and section live on its `PluginInfo` (`plugin.info`, `searx/plugins/_core.py:27`). `plugins_storage.info` is a property returning `[p.info for p in plugin_list]` (`searx/plugins/_core.py:207`).
       - `locked=sorted(prefs.cfg.lock)` — sorted for a stable response; `cfg.lock` is a `set`.
    7. Return the response with `default=default`, `current=current`, `client=doc or {}`, `skipped=skipped`, `token=token or ""`.

  The default/current snapshot order matters: `parse_dict()` mutates the setting objects in place, so the defaults must be read first.

- [ ] **Step 5: Run the tests to verify they pass**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit tests.unit.test_api_preferences -v`
  Expected: PASS

- [ ] **Step 6: Run the full unit suite**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit`
  Expected: OK

- [ ] **Step 7: Commit**

  ```bash
  git add backend/models/api.py backend/api/preferences.py tests/unit/test_api_preferences.py
  git commit -m "feat(backend): report the client preference document on GET /preferences"
  ```

---

### Task 5: POST /api/v1/preferences

**Files:**
- Modify: `backend/api/preferences.py`
- Test: `tests/unit/test_api_preferences.py`

**Interfaces:**
- Consumes: `filter_document()`, `normalize_document()`, `mint_token()` from Tasks 2–3
- Produces: `POST /api/v1/preferences` accepting a JSON object body; on success returns the `PreferencesResponse` shape with `token` set to the minted token, `client` set to the **applicable** document, `skipped` set to the notes for the keys this instance could not apply, `current` set to the effective values, and sets the `sxng_preferences` cookie.

- [ ] **Step 1: Write the failing tests**

  Append to `tests/unit/test_api_preferences.py` (which already imports `build_preferences`, `filter_document`, and `mint_token` from Task 4's import block — no new imports are needed here):

  ```python
  def test_post_returns_token():
      res = client.post("/api/v1/preferences", json={"categories": ["general"]})
      assert res.status_code == 200
      data = res.json()
      assert data["token"]
      assert data["client"]["categories"] == ["general"]
      assert data["current"]["categories"] == ["general"]


  def test_post_sets_cookie():
      res = client.post("/api/v1/preferences", json={"categories": ["general"]})
      assert "sxng_preferences" in res.cookies
      assert res.cookies["sxng_preferences"] == res.json()["token"]


  def test_post_skips_unknown_key():
      """An unknown key is skipped, not rejected -- and it is reported."""
      res = client.post("/api/v1/preferences", json={"nope": 1, "categories": ["general"]})
      assert res.status_code == 200
      data = res.json()
      # The rest of the document still applied.
      assert data["client"]["categories"] == ["general"]
      # The unknown key did not.
      assert "nope" not in data["client"]
      # And the client is told it was skipped.
      assert any("nope" in note for note in data["skipped"])


  def test_post_skips_bad_engine():
      """A template naming an engine this instance lacks still applies."""
      prefs = build_preferences()
      res = client.post(
          "/api/v1/preferences",
          json={"disabled_engines": ["no such engine__general"], "categories": ["general"]},
      )
      assert res.status_code == 200
      data = res.json()
      assert "disabled_engines" not in data["client"]
      assert data["client"]["categories"] == ["general"]
      assert any("disabled_engines" in note for note in data["skipped"])
      assert prefs  # the instance is unchanged by the request


  def test_post_rejects_tokens():
      res = client.post("/api/v1/preferences", json={"tokens": {"e": "s"}})
      assert res.status_code == 400
      assert res.json()["detail"].startswith("preferences_tokens_not_allowed")


  def test_post_rejects_non_object():
      res = client.post("/api/v1/preferences", json=[1, 2])
      assert res.status_code == 400
      assert res.json()["detail"].startswith("preferences_invalid")


  def test_post_ignores_token():
      """A token sent with POST is discarded; the body is the whole truth."""
      stale = "not-a-valid-token"
      res = client.post(
          "/api/v1/preferences",
          json={"categories": ["general"]},
          headers={"X-SearXNG-Preferences": stale},
      )
      assert res.status_code == 200
      data = res.json()
      # No 400/403/409 from the garbage token -- it was simply ignored.
      assert data["client"]["categories"] == ["general"]
      assert data["token"]
      assert data["token"] != stale


  def test_post_replaces_doc():
      """POST replaces; it does not merge with a previous token."""
      first = client.post("/api/v1/preferences", json={"categories": ["general"]})
      token = first.json()["token"]
      second = client.post(
          "/api/v1/preferences",
          json={"image_proxy": True},
          headers={"X-SearXNG-Preferences": token},
      )
      assert second.status_code == 200
      data = second.json()
      # The categories from the first document are gone.
      assert "categories" not in data["client"]
      assert data["client"]["image_proxy"] is True
  ```

  `test_post_ignores_token` is the regression for the decision that a token sent with `POST` is discarded. `test_post_replaces_doc` is the regression for replace-not-merge semantics: the second document must not inherit `categories` from the first.

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit tests.unit.test_api_preferences -v`
  Expected: FAIL — `405 Method Not Allowed`

- [ ] **Step 3: Implement POST**

  In `backend/api/preferences.py` add:

  ```python
  from typing import Any

  from fastapi import APIRouter, Body, HTTPException, Request, Response

  from searx.preferences import COOKIE_MAX_AGE
  ```

  ```python
  @router.post(
      "/preferences",
      response_model=PreferencesResponse,
      summary="Apply a client preference document",
      description=(
          "Apply a preference document and return the effective state for this "
          "client, together with a signed token that carries the document.\n\n"
          "The posted document *replaces* the client's document; nothing is "
          "merged from a previously presented token -- a token sent with this "
          "request is discarded. The token is returned in the response body and "
          "set as the `sxng_preferences` cookie.\n\n"
          "The document is applied leniently: a key this instance cannot apply "
          "(an unknown key, a value it rejects, an engine or plugin it does not "
          "have) is skipped rather than failing the request, so a template "
          "document can be reused across instances. The skipped keys are listed "
          "in the `skipped` field of the response."
      ),
      operation_id="post_preferences",
  )
  def post_preferences(
      request: Request,
      response: Response,
      body: Any = Body(...),
  ) -> PreferencesResponse:
  ```

  Handler body:

  1. `if not isinstance(body, dict):` → `HTTPException(400, detail="preferences_invalid: the preference document must be a JSON object")`. Typing the body as `dict[str, object]` instead would make FastAPI answer 422 for a JSON array, which contradicts the spec's 400 for an invalid document.
  2. `prefs = build_preferences()`.
  3. Snapshot `default = {name: setting.get_value() for name, setting in prefs.key_value_settings.items()}` **before** applying the document.
  4. `(doc, skipped) = filter_document(body, prefs)` inside `try/except PreferencesError` → `HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}")`. The only rejection here is `tokens`.
  5. `prefs.parse_dict(normalize_document(doc, prefs))` inside `try/except ValidationException` → `HTTPException(400, detail=f"preferences_invalid: {exc}")`. This is a backstop: the filter already checked each value, so reaching `ValidationException` here means the filter and upstream disagree — a bug, and a 500 would be the honest answer, but 400 with the code is the safer surface.
  6. `token = mint_token(doc, prefs)` — the token carries the **applicable** document, so the epoch is computed over exactly the pairs that will be interpreted against this instance.
  7. Set the cookie on the injected `response`:
     ```python
     response.set_cookie(
         "sxng_preferences",
         token,
         max_age=COOKIE_MAX_AGE,
         path="/",
         httponly=True,
         samesite="lax",
         secure=request.url.scheme == "https",
     )
     ```
  8. Return `PreferencesResponse(...)` with `default` (snapshot before parsing), `current` (snapshot after), `client=doc`, `skipped=skipped`, `locked=list(prefs.cfg.lock)`, `token=token`, and the same catalogue fields the GET route returns.

  **The handler never reads a presented token.** It does not call `read_client_token` and does not call `verify_token` — a token accompanying the request is ignored and discarded (see **Decisions taken** §2). The body is the entire source of truth.

  Setting the cookie on the injected `response` works with `response_model=PreferencesResponse`: FastAPI copies the injected response's headers onto the serialized body (verified — the `Set-Cookie` header survives alongside the JSON body).

  Extract the catalogue assembly (categories / engines / locales / formats / styles / plugins / engine_pairs) and the `default`/`current` snapshots into module-level helpers so GET and POST share one implementation — they must not drift.

- [ ] **Step 4: Run the tests to verify they pass**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit tests.unit.test_api_preferences -v`
  Expected: PASS

- [ ] **Step 5: Run the full unit suite**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit`
  Expected: OK

- [ ] **Step 6: Commit**

  ```bash
  git add backend/api/preferences.py tests/unit/test_api_preferences.py
  git commit -m "feat(backend): add POST /api/v1/preferences to apply client documents"
  ```

---

### Task 6: Reset semantics without a DELETE route

There is no `DELETE` route (see **Decisions taken** §1). This task pins the two ways a client resets its state, so the absence of the route is a documented behavior rather than a gap.

**Files:**
- Modify: `backend/api/preferences.py`
- Test: `tests/unit/test_api_preferences.py`

**Interfaces:**
- Consumes: `mint_token()` from Task 3
- Produces: no new route. `POST` with an empty document `{}` is the reset operation; it returns `client = {}`, `token` = a signed token of the empty document, and sets that token as the cookie.

- [ ] **Step 1: Write the failing tests**

  Append to `tests/unit/test_api_preferences.py`:

  ```python
  def test_post_empty_document_resets():
      """POST {} is the reset operation that DELETE would have been."""
      res = client.post("/api/v1/preferences", json={})
      assert res.status_code == 200
      data = res.json()
      assert data["client"] == {}
      assert data["token"]
      assert data["current"] == data["default"]


  def test_post_empty_resets_cookie():
      client.post("/api/v1/preferences", json={"categories": ["general"]})
      res = client.post("/api/v1/preferences", json={})
      assert res.cookies["sxng_preferences"] == res.json()["token"]
      # The new cookie carries the empty document.
      prefs = build_preferences()
      doc, skipped = verify_token(res.json()["token"], prefs)
      assert doc == {}
      assert skipped == []


  def test_no_delete_route():
      """There is deliberately no DELETE route."""
      res = client.delete("/api/v1/preferences")
      assert res.status_code == 405
  ```

  `test_no_delete_route` is a deliberate negative test: it pins the decision that the route does not exist, so a future change that reintroduces it has to update this test on purpose.

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit tests.unit.test_api_preferences -v`
  Expected: `test_post_empty_document_resets` and `test_post_empty_resets_cookie` PASS (the behavior falls out of Task 5); `test_no_delete_route` PASSES as well. This task verifies existing behavior and adds a regression test — it may require no production change. If `test_post_empty_document_resets` fails, Task 5's implementation does not handle the empty document correctly; fix Task 5, not this task.

  The `verify_token` import must be added to the test module's import block from Task 4.

- [ ] **Step 3: Document the reset path in the route description**

  In `backend/api/preferences.py`, extend the POST route's `description` with a sentence: "To reset to the instance defaults, POST an empty document `{}`; the response carries a signed token of the empty document." Also mention in the module or route description that no `DELETE` route exists and why (an API client stops sending the token; a browser client clears the cookie).

- [ ] **Step 4: Run the tests to verify they pass**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit tests.unit.test_api_preferences -v`
  Expected: PASS

- [ ] **Step 5: Run the full unit suite**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit`
  Expected: OK

- [ ] **Step 6: Commit**

  ```bash
  git add backend/api/preferences.py tests/unit/test_api_preferences.py
  git commit -m "test(backend): pin POST {} as the preference reset operation"
  ```

---

### Task 7: Wiring, contract regression, and docs

**Files:**
- Modify: `backend/app.py`, `backend/openapi.py`
- Create: `tests/regression/test_api_preferences_token.py`
- Modify: `docs/superpowers/specs/2026-11-17-client-preferences-design.md`, `/opt/data/obsidian/SearXNG Client Preferences Design (11-17).md`

**Interfaces:**
- Consumes: the finished routes from Tasks 4–6
- Produces: CORS that permits credentials; a published OpenAPI description matching the implemented contract; a regression test pinning the token envelope and error codes; the spec and its vault copy amended to match the two changed decisions.

- [ ] **Step 1: Write the failing regression test**

  Create `tests/regression/test_api_preferences_token.py`:

  ```python
  # SPDX-License-Identifier: AGPL-3.0-or-later
  """Contract tests for the client preference token envelope."""

  # pylint: disable=missing-module-docstring,disable=missing-class-docstring,invalid-name

  import json
  import zlib
  import base64

  from fastapi.testclient import TestClient

  from backend.app import app

  client = TestClient(app)


  def test_token_envelope_shape():
      res = client.post("/api/v1/preferences", json={"categories": ["general"]})
      assert res.status_code == 200
      token = res.json()["token"]
      payload, _, signature = token.partition(".")
      assert len(signature) == 64
      raw = zlib.decompress(base64.urlsafe_b64decode(payload))
      body = json.loads(raw)
      assert set(body) == {"epoch", "doc"}
      assert len(body["epoch"]) == 16
      assert body["doc"]["categories"] == ["general"]


  def test_error_codes_are_stable():
      res = client.post("/api/v1/preferences", json={"tokens": {"e": "s"}})
      assert res.status_code == 400
      assert res.json()["detail"].startswith("preferences_tokens_not_allowed")


  def test_bad_token_is_400_not_500():
      """A garbage token must be a 400, never an unhandled 500."""
      for bad in ("abc", "eyJ9", "eJzLyy9RyCrOzwMADiYDLA==", "not-a-token"):
          res = client.get("/api/v1/preferences", headers={"X-SearXNG-Preferences": bad})
          assert res.status_code == 400, f"{bad!r} gave {res.status_code}"
          assert res.json()["detail"].startswith("preferences_invalid")
  ```

- [ ] **Step 2: Run the regression suite**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/regression -v`
  Expected: PASS (the contract already holds; this test pins it)

- [ ] **Step 3: Widen CORS**

  In `backend/app.py`:

  ```python
      app.add_middleware(
          CORSMiddleware,
          allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
          allow_methods=["GET", "POST"],
          allow_headers=["*"],
          allow_credentials=True,
      )
  ```

  `allow_credentials=True` is required for the browser to accept the `sxng_preferences` cookie cross-origin (the SPA runs on `:5173`, the API on `:8080`). `DELETE` is **not** in `allow_methods` — there is no DELETE route (see **Decisions taken** §1).

- [ ] **Step 4: Update the OpenAPI descriptions**

  In `backend/openapi.py`, extend the `preferences` tag description to mention the write route and the client-held token. Keep the word "languages" out of it — `tests/unit/test_api_openapi.py::test_preferences_description_matches_the_response_model` asserts the description never advertises the removed `languages` field.

  In `backend/api/preferences.py`, the GET route's `description=` must likewise not advertise a field the payload does not carry — it now carries `client`, `skipped` and `token`, so describe them.

- [ ] **Step 5: Run both suites and the lint gates**

  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/unit`
  Run: `env -u PYTHONPATH .venv/bin/python -m nose2 -s tests/regression`
  Run: `env -u PYTHONPATH .venv/bin/python -m black --check --diff --target-version py311 --line-length 120 --skip-string-normalization backend tests`
  Run: `env -u PYTHONPATH .venv/bin/python -m pylint --rcfile .pylintrc backend tests`
  Expected: all OK / clean

- [ ] **Step 6: Validate the OpenAPI document**

  Run: `env -u PYTHONPATH .venv/bin/python -c "from backend.app import app; app.openapi()"` and confirm `/api/v1/preferences` carries exactly `get` and `post` operations, each with an explicit `operationId`, and **no** `delete` operation.
  Expected: no exception; two operations present; `delete` absent.

- [ ] **Step 7: Amend the spec and the vault note**

  The two changed decisions (see **Decisions taken**) mean the spec no longer matches the implementation. Amend **both** `docs/superpowers/specs/2026-11-17-client-preferences-design.md` and the vault note `/opt/data/obsidian/SearXNG Client Preferences Design (11-17).md` (the user's copy — mirror the change there, keeping its `Links:` header and `> Next:` footer):

  1. **§3 — withdraw the `DELETE` route.** Remove the `DELETE /api/v1/preferences` subsection. State the replacement: `POST` with an empty document `{}` mints a token of the empty document and resets the client; an API client may simply stop sending its token; a browser client may clear its cookie.
  2. **§4 — replace "Unknown keys → 400" with the lenient rule.** State that a key this instance cannot apply is skipped and reported in the response's `skipped` list; that `tokens` remains the one hard rejection; that `default_category` "present but unknown → 400" becomes "present but unknown → skipped" (and absent still means `general`); and note the trade-off explicitly (a typo is indistinguishable from a key belonging to another instance, which is why the skip is reported). The spec's stated reason for the old rule — "a typo'd preference key silently doing nothing is the same failure class as the `.time_range` bug" — should be preserved in the amended text as the reason the skip is *reported*, not the reason the request fails.
  3. **§5 — narrow the error table to the endpoints that consume a token.** `GET` (and, later, the search route) honor 400/403/409. `POST` consumes no token: a token sent with it is ignored and discarded.
  4. **§5 — amend the epoch definition** to say it is computed over the *applied* (filtered) document, and that a named pair or plugin the instance no longer has contributes `null`, making the token stale rather than causing an error.
  5. **§7 step 7** — keep the stale-token recovery path; it now works because `POST` ignores the stale token rather than rejecting it.
  6. **§10 Q2** — amend "every endpoint that consumes a token honors 409" to note that `POST` consumes no token.
  7. **§11** — mark items 1 and 2 as implemented (with the commit sha range), and record the decisions this plan pinned that the spec left open (see **Open decisions**).
  8. **§2 "Settled decisions" table** — the `Routes` row currently reads "`GET` + `POST` + `DELETE /api/v1/preferences`"; change it to "`GET` + `POST /api/v1/preferences`", and the `Validation` row (if present) to the lenient rule.
  9. **§9 Risks** — the line "CORS gains `allow_credentials=True` + `DELETE`" becomes "`allow_credentials=True`" only.
  10. **§10 Builder check** — Q5 (DELETE semantics) is withdrawn; Q8 ("POST handling of `tokens` / unknown keys / bad `default_category`") is amended to record the lenient rule; Q2 (what goes into the epoch) is amended to say the epoch covers the *applied* document.

- [ ] **Step 8: Commit**

  ```bash
  git add backend/app.py backend/openapi.py tests/regression/ docs/superpowers/specs/
  git commit -m "feat(backend): wire the client preference routes into CORS and the OpenAPI contract"
  ```

---

## Blocking dependency (not in this plan)

**The production `secret_key` guard (spec §11 item 4).** The tokens minted here are signed with `settings["server"]["secret_key"]`, and `searx/settings.yml` still ships the default `"ultrasecretkey"`. Until `backend/services/kernel.py` enforces upstream's production check (`searx/webapp.py:1380`), every token this plan mints is forgeable by anyone who knows the default. This is spec §11 item 4 and is deliberately **not** a task here, because upstream's check calls `sys.exit(1)` at startup — applying it to the backend as-is would stop the local dev instance from booting. The operator decision (enforce, or warn-and-continue, and how a dev instance opts out) needs to be made before the endpoint is exposed to anyone but the operator.

## Open decisions (need the user's confirmation)

1. **Do POST and DELETE validate a presented client token?** — **RESOLVED by the user on 2026-11-19: no token verification on POST, and no DELETE route at all.** See **Decisions taken** §§1–2.
2. **Strict validation vs lenient application.** — **RESOLVED by the user on 2026-11-19: lenient (option B).** See **Decisions taken** §3. The one consequence to be aware of is that a typo'd key is now indistinguishable from a key belonging to another instance; the `skipped` list in the response is what mitigates it. If strict validation is ever wanted back, the natural switch is a `strict` flag on `filter_document` — the plan's structure keeps that to one function.
3. **Legacy upstream-format blobs.** Spec §5 says a "legacy blob keeps working". The fork's own tokens have never shipped, so the only legacy blobs are upstream's `get_as_url_params()` format (base64url of zlib of a *urlencoded* string, not JSON) — which upstream also puts engine `tokens` in. This plan supports only the fork's JSON envelope; accepting upstream's format needs a second parser and a decision on the `tokens` it carries (reject the blob, or drop the `tokens` key). Deferred until requested.
4. **Cookie `Max-Age`.** The spec says `315360000` and calls it "5y, matches `COOKIE_MAX_AGE`" — but upstream's `COOKIE_MAX_AGE` is `157680000` (5 years), and `315360000` is 10 years. This plan uses the upstream constant `searx.preferences.COOKIE_MAX_AGE` so the two cannot drift. Confirm, or say to hard-code the spec's literal value instead.
5. **Fork-side engine/plugin locks (spec §6).** `preferences.lock_engines` / `lock_plugins` **cannot** be added under `preferences:` in `settings.yml`: `searx/_settings.py`'s `SettingsPref` is a msgspec Struct with `forbid_unknown_fields=True`, so `apply_schema` rejects the file with "Invalid settings.yml" and the instance will not boot. Unknown *top-level* settings keys are accepted, so a fork-owned top-level key is the viable location. This plan's epoch uses the lock set that exists today (`prefs.cfg.lock`); the §6 locks need their own settings decision before they can be enforced.

## Self-review

- **Spec coverage:** §3 routes → Tasks 4–6 (GET client-aware, POST replace-and-apply, reset-without-DELETE, response shape). §4 document schema, normalization, `tokens` exclusion, lenient application → Task 2. §5 token envelope, signing, epoch, transports, error table → Tasks 3–4. §6 fork-side locks → **deferred** (Open decision 5); upstream key-value lock handling is upstream's own `parse_dict()` behavior and is inherited unchanged. §7 merge order and per-request `Preferences` population → Task 4 (GET) and Task 5 (POST). §11 items 1–2 → this plan; items 3–7 → deferred. The three spec sections that the changed decisions invalidate (§3's DELETE, §4's unknown-key rejection, §5's endpoint-wide error table) are amended in Task 7 Step 7.
- **Step scan:** every step names one action with a checkable result; no "TBD" or "handle edge cases" placeholders. Where the spec left a value open, the value is pinned in **Global Constraints**, **Decisions taken**, or **Open decisions**.
- **Type consistency:** `PreferencesError(status_code, code, message)` is raised by the service and mapped to `HTTPException(status_code=..., detail=f"{code}: {message}")` at the route boundary; `filter_document(raw, prefs)` returns `(applicable, skipped)`; `normalize_document(doc, prefs)` consumes the applicable document; `mint_token(doc, prefs)` consumes the same document; `decode_token(token)` returns `(doc, epoch, signed)`; `verify_token(token, prefs)` returns `(filtered_doc, skipped)`; `sign_payload(payload)` is the single signing entry point. `read_client_token` is defined in the **service** (Task 4) and imported by the route — and used by `GET` only, never by `POST`.
- **Review Focus:** all nine items are pinned by a named test in the owning task (Task 2 → items 1–3 and 5, Task 3 → items 4, 8 and 9, Task 4 → item 6, Task 5 → item 7).
- **Proportion:** the plan is roughly the size of the spec section it implements; code bodies are given only where the algorithm is not determined by the signature and the tests (the epoch payload shape, the transport precedence, the cookie attributes).

### External review

Two independent reviewer passes checked this plan against the spec and the repo.

**Pass 1** confirmed the upstream facts (the `disabled_engines` dispatch trap, the `MAP_STR2BOOL` `KeyError`, the `SearchLanguageSetting` silent coercion, `default_choices` as the pre-parse snapshot, `COOKIE_MAX_AGE = 157680000`, the four duplicated builders, the injected-`Response` cookie surviving `response_model`, and Starlette's `delete_cookie` emitting `Max-Age=0`). Its findings and the fixes applied:

- **Fixed:** `read_client_token` placement (it was declared in the route module but imported from the service — now defined in the service); `decode_token`'s arity (`token` only, with `verify_token` re-partitioning the token instead of relying on out-of-scope names); the `_sign` vs `sign_payload` naming split; the missing import blocks in both test modules; the undefined `_other_epoch` helper; `test_enabled_only_applies` naming an engine pair that does not exist; the payload-size guard rejecting a payload of exactly the limit; the `safesearch` bool-is-an-int trap; the baseline test count (402 → 403); the `function-rgx` description; the unit-registry description; the `categories: ["none"]` sentinel (now stated as deliberate); the DELETE cookie assertion (the test client drops an expired cookie from its jar, so the assertion is on the raw header); the non-object POST body (now `400`, not FastAPI's 422); the kernel-init dependency of the new service test module; and the `read_client_token` home in the File Structure table.
- **Raised as a blocking decision, not silently resolved:** whether POST and DELETE verify a presented token. The spec's §5 error table and §7 step 7 contradict each other; the plan states the recommendation (only GET verifies) and the condition under which the stricter reading applies. **Resolved by the user on 2026-11-19** — see **Decisions taken**.
- **Confirmed as out of scope and reported for ratification:** the cookie `Max-Age` discrepancy, legacy upstream blob support, and the fork-side lock settings surface — see **Open decisions**.

**Pass 2** ran against the plan as revised for the three user decisions, and verified the lenient-filter and token algorithms end-to-end against the live repo (a working prototype of `filter_document`, `normalize_document`, `compute_epoch`, `mint_token`, `verify_token` and both routes was executed against the real upstream classes). Its findings and the fixes applied:

- **Fixed — a malformed token was a 500, not a 400.** `base64.urlsafe_b64decode` raises `binascii.Error` ("Incorrect padding" on `"abc"`), `zlib.decompressobj().decompress` raises `zlib.error`, and `json.loads` raises `json.JSONDecodeError`. The plan promised `400 preferences_invalid` for a malformed token but never said to catch these. Task 3 now requires wrapping the whole decode, and two tests pin it (`test_undecodable_token_rejected`, `test_bad_token_is_400_not_500`).
- **Fixed — a `KeyError` (500) on an engine removed from the instance.** `compute_epoch` used `default_choices[pair]`; the plan's own "vanished pair" scenario would therefore raise instead of reporting stale. Now `.get(pair)`, so the pair contributes `null` and the token is rejected as stale. Pinned by `test_vanished_pair_is_stale`.
- **Fixed — the boolean normalization was underspecified.** `MAP_STR2BOOL` maps `"none"` to `False`, so a truthiness test would normalize it to `"1"`. The rule now says to look the string up in `MAP_STR2BOOL`; pinned by `test_boolean_normalization`.
- **Fixed — a lint-gate failure from unused imports.** The service module's import list included `ValidationException` (used only in the route module) and `Any` (unused). The repo's `.pylintrc` does not disable `unused-import` (W0611), so implementing the list verbatim fails the gate (verified by running pylint against the repo's own config). The service now imports exactly six names.
- **Fixed — a lint-gate failure from a missing import.** `read_client_token` lives in the service module and annotates `request: Request`, but the service module's import list had no `from fastapi import Request`; pylint reports `undefined-variable` (E0602) on the annotation (verified). The import is now specified.
- **Fixed — seven test names exceeded `function-rgx`.** The repo's regex caps a function name at 31 characters and `invalid-name` (C0103) is not disabled (verified). All seven were renamed to fit; the longest is now exactly 31.
- **Fixed — an unbalanced code fence.** A patch had left Task 2's test bodies outside their code fence, so the document's markdown was malformed. All 68 fences now balance and all 27 Python blocks parse (checked with `ast.parse`).
- **Confirmed sound:** the lenient filter's per-key rules (each value class is classified correctly and no rejected value reaches `parse_dict()`); `POST` ignoring a presented token; replace-not-merge semantics; `POST {}` as the reset; the `tokens` hard rejection; the epoch's stability across a roundtrip; and the `enabled_engines`-only dispatch trap.
