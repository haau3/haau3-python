# Contributing

Issues are welcome: a bug, a confusing name, a missing example.

## Working on the SDK

```sh
uv sync
uv run pytest
uv run mypy
uv run pyright
uv run ruff check && uv run ruff format --check
uv build && uv run python scripts/check_package.py dist
```

[uv](https://docs.astral.sh/uv/) installs Python and every dependency. It installs no version of
anything published in the last three days (`exclude-newer` in `pyproject.toml`). The tests run on
Python 3.10 to 3.15, and the async ones on both asyncio and trio.

## Generated code

These are written by `scripts/generate.py` from `openapi/openapi.json`, the pinned copy of
`https://api.haau3.com/openapi.json`, and from `fhir/fhir.schema.json.zip`, HL7's JSON Schema for
FHIR R4 (4.0.1), pinned by its sha256. Never edit them by hand:

- `src/haau3/_methods.py`: one method per operation, on both clients and on each `with_response`;
- `src/haau3/_operations.py`: each operation's method, path and the FHIR resource types it answers with;
- `src/haau3/calls.py`: the entries of a batch;
- `src/haau3/types.py`: the public type names, and each operation's `Query`, `Body` and `Response`;
- `src/haau3/fhir.py`: the FHIR R4 TypedDicts, by `scripts/fhir_emit.py`;
- `src/haau3/_components.py`: the API's other components, by
  [datamodel-code-generator](https://github.com/koxudaxi/datamodel-code-generator);
- `api.md`;
- `openapi/code-samples.json`: an [OpenAPI Overlay](https://spec.openapis.org/overlay/latest.html)
  that adds a Python sample to every operation as `x-codeSamples`, for a documentation site to show
  beside it. Each sample is a whole program that sends the operation's example request from the spec
  and prints one field of the answer. `tests/test_samples.py` runs each one as it is written, with
  `HAAU3_BASE_URL` pointing at a stand-in that answers only the example request, and CI type-checks
  them with the README's blocks (`scripts/readme_blocks.py`).

To take a new spec:

```sh
uv run python scripts/fetch_spec.py
uv run python scripts/generate.py
```

CI generates again (`scripts/generate.py --check`) and fails if the result differs from what is
committed. A Dependabot update of datamodel-code-generator or ruff can change the output, so run the
generator on its branch too.

`calls.py` offers every operation on the FHIR base but the ones a batch cannot carry, which the
generator names in `NOT_IN_A_BATCH` with the spec's own words. It stops if one of those leaves the
spec, or if a query parameter's Python name would clash with another or with a per-call option.

The FHIR types come from HL7's own schema rather than a FHIR library, because none is maintained for
R4 as TypedDicts: `fhir.resources` ships R4B and R5, and validates. datamodel-code-generator renames
FHIR's `_x` keys, which a TypedDict must keep, so `scripts/fhir_emit.py` writes them.

## When the API changes

`.github/workflows/regenerate.yml` fetches the spec every morning, and can be run by hand
(`gh workflow run regenerate.yml`). When the spec changed, it regenerates the SDK and opens one pull
request as haau3-bot, or updates the one already open. It never overwrites a commit it did not
make, so a fix pushed to its branch stays.

[oasdiff](https://github.com/oasdiff/oasdiff) compares the pinned spec with the new one, and a change
it grades as an error or a warning can break callers: the PR is labelled `breaking` and titled
`feat!:`. `openapi/oasdiff-levels.txt` changes six of oasdiff's grades (the file takes no comments,
so the reasons are here):

- three changes break a typed caller although they do not break an HTTP one, so each is an error: a
  changed operationId (`api-operation-id-removed`) renames a method, a removed optional response
  property (`response-optional-property-removed`) stops code that reads it type-checking, and a
  removed component (`api-schema-removed`) removes an exported type;
- three rules compare `info.version` as a release number and misread `1.0.0-preview.N`, so they are
  off (`api-version-decreased`, `api-version-not-bumped`, `api-major-version-not-bumped`).

If neither the generated code nor the samples changed, the PR is titled `chore:` and releases nothing. To run oasdiff
yourself, against the spec on `main`:

```sh
oasdiff changelog old.json openapi/openapi.json --severity-levels openapi/oasdiff-levels.txt
```

## Pull requests and releases

Pull requests are squash-merged, and a PR's title becomes its commit on `main`.
[release-please](https://github.com/googleapis/release-please) reads those titles and keeps one
release PR open with the next version and its changelog:

- `feat: ...` and `fix: ...` release a patch, 0.1.1 to 0.1.2.
- `feat!: ...` releases a minor, 0.1.1 to 0.2.0. While the SDK is `0.y.z`, a minor is the only
  release that may change what a method takes or answers.
- Any other title (`docs: ...`, `chore: ...`, `README: ...`) releases nothing.

The title is the line a reader sees in `CHANGELOG.md`, so it says what changed for someone using the
SDK. Like every file, commit message and PR description here, it says only what this repository
shows: CI's "Public text" check (`python3 scripts/check_public_text.py`) fails on anything that
refers elsewhere, such as an issue number without its repository.

The version lives in one place, `src/haau3/_version.py`, which release-please writes. Merging the
release PR makes the GitHub Release and its tag, and `release.yml` builds that version, then waits
in the `pypi` environment. Once a maintainer approves it there, PyPI's own action publishes it with
attestations, through PyPI's trusted publishing: no PyPI token exists anywhere.

## Tests

The tests run against httpx2's `MockTransport`, and a few against local sockets that refuse, hang or
answer slowly. `tests/test_live.py` calls the production API, and runs only with
`HAAU3_LIVE_TEST=1` and a key in `HAAU3_API_KEY`.

`tests/test_privacy.py` plants a marker in every body and renders each error every way it is
commonly printed, including a traceback with each frame's local variables. Keep a body in a
`Hidden` from the moment it is read, and never bind a revealed one to a name in a frame that can
still raise.

`tests/typecheck/consumer.py` is a customer's code: CI type-checks it with mypy and pyright, both
strict, on Python 3.10, against the built wheel. A line carrying both checkers' ignore comments is a
mistake each must catch.
