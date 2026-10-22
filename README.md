# haau3

The Python SDK for the [haau3 platform API](https://docs.haau3.com), as typed calls:

- **Scheduling:** open appointment slots from many SMART Scheduling Links publishers, in one search.
- **Medical Benefits:** Medicare Advantage and ACA Marketplace plans as FHIR `InsurancePlan`,
  searchable by state or county, and which providers are in a plan's network.
- **MedList:** one reconciled medication list from one patient's FHIR medication records.

On the same key and FHIR base: RxNorm and LOINC lookups, value sets and code validation
(Terminology), practitioners and organizations from CMS's National Provider Directory (Provider
directory), and standard FHIR Questionnaires such as the PHQ-9.

The haau3 platform API is in preview: it may change as we build, usually to add features or improve performance.

Also for TypeScript: [`@haau3/sdk`](https://www.npmjs.com/package/@haau3/sdk), and for Go:
[`github.com/haau3/haau3-go`](https://pkg.go.dev/github.com/haau3/haau3-go).

## Install

```sh
pip install haau3
```

Python 3.10 or newer. The SDK runs on a server only: it refuses to start in a browser (Pyodide),
where your API key would be public.

## Authenticate

Create an API key on [platform.haau3.com](https://platform.haau3.com), then set it as
`HAAU3_API_KEY` or pass it as `api_key`.

```python
from haau3 import Haau3

client = Haau3()  # reads HAAU3_API_KEY
```

Make one client and reuse it: it holds a pool of connections, and is safe to share between threads.

## A first call

```python
plan = client.read_insurance_plan("H2406-013-000")
print(plan.get("name"))  # "AARP Medicare Advantage from UHC FL-0021 (PPO)"
```

An answer is the JSON the API sent, as plain dicts and lists, so it goes into `json.dumps`, a
DataFrame or a database as it is. Each is typed with a `TypedDict`, so a type checker and your
editor know every key: FHIR answers with HL7's R4 types (`haau3.fhir`), the other services' answers
with the API's own (`haau3.types`). FHIR elements are optional, so read them with `.get()`.

## From async code

`AsyncHaau3` has the same methods, awaited. It runs on asyncio and on trio.

```python
from haau3 import AsyncHaau3

async_client = AsyncHaau3()
plan = await async_client.read_insurance_plan("H2406-013-000")
```

Make one per event loop and reuse it, as FastAPI's lifespan does (see "Using it with FastAPI,
Django and Flask"); it must not outlive its event loop.

## One call per service

```python
# Scheduling: open appointment slots, here in Boston.
found = client.search_slots(city="Boston", state="MA", limit=10)
print(found["total"], len(found["slots"]))

# Provider directory: a practitioner by NPI, from CMS's National Provider Directory.
practitioners = client.search_practitioner(identifier="1093209884")

# Terminology: a LOINC code's display and properties.
glucose = client.code_system_lookup(system="http://loinc.org", code="2345-7")

# Questionnaires: a form by its id, such as the PHQ-9.
phq9 = client.read_questionnaire("44249-1")

# Medical Benefits: a Medicare Advantage or ACA Marketplace plan, and which providers are in its network.
plan = client.read_insurance_plan("H2406-013-000")
network = client.check_insurance_plan_network("H3359-038-000", npi=["1518929058"])

# Pharmacy Benefits: what a medication would cost, as the CARIN pharmacy benefit check, from a FHIR
# message Bundle. A member's check carries patient data, so send it only under an executed agreement.
checked = client.process_message(message)

# MedList: one reconciled medication list from one patient's FHIR medication records. It receives
# patient data, so send it only under an executed agreement.
reconciled = client.reconcile_medications(records)
```

Every method, grouped by service, with its request and a link to its page on docs.haau3.com, is in
[api.md](https://github.com/haau3/haau3-python/blob/main/api.md). Each is named by its operationId
in snake_case, and so is each query parameter: `name:contains` is `name_contains`, `_count` is
`count`, `serviceTypeCode` is `service_type_code`. Each method's docstring gives both names.

## With an AI coding agent

An agent can read the docs directly:

- [docs.haau3.com/llms.txt](https://docs.haau3.com/llms.txt) lists every docs page as Markdown.
- [docs.haau3.com/skill.md](https://docs.haau3.com/skill.md) is an
  [Agent Skill](https://agentskills.io/specification) that says what the platform does and how to call
  it. `npx skills add https://docs.haau3.com` adds it to your project's coding agents.
- `https://docs.haau3.com/mcp` is an MCP server that searches the docs. To add it:
  - Claude Code: `claude mcp add --transport http haau3 https://docs.haau3.com/mcp`
  - Codex: `codex mcp add haau3 --url https://docs.haau3.com/mcp`
  - Cursor, in `.cursor/mcp.json`: `{ "mcpServers": { "haau3": { "url": "https://docs.haau3.com/mcp" } } }`
  - VS Code: `code --add-mcp '{"name":"haau3","type":"http","url":"https://docs.haau3.com/mcp"}'`

## Options

| Option | Default | |
|---|---|---|
| `api_key` | `os.environ["HAAU3_API_KEY"]` | Your platform API key. |
| `base_url` | `os.environ["HAAU3_BASE_URL"]`, then `https://api.haau3.com` | Where the API is. The API key is sent there only. |
| `timeout` | `65.0` | Seconds one attempt may take. |
| `max_retries` | `1` | Retries after a timeout, a network failure, a 429 or a 5xx. |
| `http_client` | a new `httpx2.Client` (`httpx2.AsyncClient` for `AsyncHaau3`) | Your own, for a proxy, certificates or connection limits. |

Each call takes `timeout`, `max_retries`, `correlation_id` and `extra_headers` as keywords, for
that call only:

```python
client.search_slots(state="MA", timeout=10, max_retries=0)
```

`http_client` passes anything [httpx2](https://github.com/pydantic/httpx2) takes: a proxy,
certificates, limits, event hooks, a transport. The SDK's own method, URL, headers, body and
timeout always win, it never follows a redirect, and it never closes a client you pass.

<!-- not run: it needs a proxy -->
```python
import httpx2

proxied = Haau3(http_client=httpx2.Client(proxy="http://proxy.internal:8080"))
```

httpx2 checks the API's certificate against your operating system's trust store, or against the
file `SSL_CERT_FILE` names. In a slim container with no CA certificates, install them, set
`SSL_CERT_FILE`, or pass `http_client=httpx2.Client(verify=ssl.create_default_context(cafile=...))`.

A retry waits a short, growing interval, or what the API's `Retry-After` header says when that is
60 seconds or less. A longer `Retry-After` fails at once, carrying it. Nothing is cached: every call
asks the API.

An async attempt ends at its timeout exactly. A sync attempt's timeout bounds connecting and each
read, and is checked between parts of the answer, so one read that stalls can run past it by up to
the timeout again.

## Errors

Every error is a `Haau3Error`, with the `request_id` the API answered with. There are three kinds:

```python
from haau3 import AuthError, NoAnswerError, RefusedError

try:
    client.read_insurance_plan(plan_id)
except RefusedError as error:
    print(error.status)  # 400, 404, 410 and so on
    print(error.outcome)  # the OperationOutcome; Scheduling's errors carry `problem`
except NoAnswerError as error:
    print(error.reason)  # "timeout", "network", "rate-limited", "unavailable", "server-error" or "unreadable"
    print(error.retry_after)  # seconds, when the API said
    print(error.detail)  # for a timeout or a network failure: what failed, such as "ConnectError: Connection refused"
except AuthError:
    pass  # the key was missing, unknown or revoked
```

No answer is never reported as an empty result, and an empty result is never an error. Every error
the API answered carries `attempts` and `duration_ms` too.

An error's message never includes the body the API answered with, and the body (`outcome` or
`problem`) is never printed when the error is: not by `str()`, `repr()` or a traceback, and not
among a traceback's local variables, which some error reporters record. A request body you sent is
held the same way. An error pickles, for Celery or multiprocessing, with every field but its body.

A FHIR answer is checked against the resource types its operation answers with. Anything else, a
`Bundle` where an `InsurancePlan` was asked for, say, is a `NoAnswerError` with reason
`"unreadable"`. The check is of the resource type, not of every element inside it: those are typed
as FHIR R4 declares them, and the SDK does not validate each one.

`error.to_dict()` gives an error as a dict that is safe to log: its name, its message and the fields
above that are set, and never the body or the cause.

```python
import logging

from haau3 import Haau3Error

try:
    client.read_insurance_plan(plan_id)
except Haau3Error as error:
    logging.getLogger(__name__).warning("the plan read failed", extra={"haau3": error.to_dict()})
```

## Request ids and timing

`client.with_response` has every method again, each returning a `Response`: the answer as `data`,
with the request id and timing that came with it, in the same names `request()` uses.

```python
response = client.with_response.read_insurance_plan(plan_id)
print(response.data.get("name"), response.request_id, response.duration_ms, response.attempts)
```

Quote the `request_id` when you ask for help: it finds the request in the API's own logs.

To find a request by an id of your own, such as your job's or your trace's, pass it as
`correlation_id`. It must be a UUID, which is all the API keeps; anything else is refused before it
is sent. The API keeps it with its record of the request, and it comes back on the answer and on
every error the API answered:

```python
response = client.with_response.read_insurance_plan(plan_id, correlation_id=job_id)
print(response.correlation_id)
```

## Paging

`pages()` yields a FHIR search's first page and every page after it, following each `next` link on
the client's own host:

```python
first = client.search_insurance_plan(coverage_area_state="NC")
for page in client.pages(first):
    for entry in page.get("entry", []):
        resource = entry.get("resource")
        if resource is not None:
            print(resource.get("id"))
```

`resources()` yields every resource the search found, across every page:

```python
for resource in client.resources(first):
    if resource["resourceType"] == "InsurancePlan":
        print(resource.get("name"))
```

It yields each entry whose search mode is `match`, or says none. A resource an `_include` or
`_revinclude` brought, and a search's own `OperationOutcome`, are not among them; `pages()` gives
every entry. From async code, `async for page in async_client.pages(first)` and `async for resource
in async_client.resources(first)`. Scheduling's searches page with `limit` and `offset`, and answer
with a `total`.

## Several requests in one

`batch()` sends several operations of the FHIR base as one request. Each entry comes from
`haau3.calls`, whose functions are called as the client's methods of the same name are, and gets its
own result, in the order given:

```python
from haau3 import BatchOk, calls

plan_result, counties_result = client.batch(
    calls.read_insurance_plan("H2406-013-000"),
    calls.list_insurance_plan_counties(state="NC"),
)

if isinstance(plan_result, BatchOk):
    print(plan_result.data.get("name"))  # an InsurancePlan
else:
    print(plan_result.error)  # a RefusedError or a NoAnswerError, as read_insurance_plan alone would have raised
```

A result is a `BatchOk` (`data`, `status`) or a `BatchError` (`error`), typed by position for up to
six entries; `match result: case BatchOk(data=data): ...` works too. Each also has `ok`, `True` or
`False`, which reads well in a log; `isinstance` and `match` are what tell a type checker which it
is. A list built in code works the same way:

```python
lookups = client.batch(
    *(calls.code_system_lookup(system="http://loinc.org", code=code, property="SHORTNAME") for code in codes)
)
```

Each entry is checked as the call alone is, and fails alone while the others answer. An entry that
fails is not retried. The batch itself is retried, and raises, as any call does, and
`client.with_response.batch(...)` gives its request id. The API takes up to 100 entries in up to 1
MiB and gives a batch 60 seconds, so send a longer list as several batches. Every operation on the
FHIR base can go in a batch except `fhir_batch`, `process_message` and `reconcile_medications`, which
the spec marks `x-haau3-batchable: false`.

To send a batch `Bundle` you built yourself, `fhir_batch()` takes it and returns the
`batch-response` as it is.

## Any request

`request()` sends a request as you give it and returns what the API answered, whatever the status,
with its headers, request id, attempts and duration:

```python
raw = client.request("GET", "/v1/scheduling/publishers")
print(raw.status, raw.reason_phrase, raw.request_id)
```

## Using it with FastAPI, Django and Flask

Each of these is a complete, tested example in [examples/](https://github.com/haau3/haau3-python/tree/main/examples):

- **FastAPI** ([fastapi_app.py](https://github.com/haau3/haau3-python/blob/main/examples/fastapi_app.py)):
  an `AsyncHaau3` made in the app's lifespan, handed to each route by a dependency, and closed when
  the app stops.
- **Django** ([django_view.py](https://github.com/haau3/haau3-python/blob/main/examples/django_view.py)):
  one `Haau3` for the whole process, shared by every request and thread.
- **Flask** ([flask_app.py](https://github.com/haau3/haau3-python/blob/main/examples/flask_app.py)):
  one `Haau3` on the app. Flask runs each async view in an event loop of its own, which an
  `AsyncHaau3` must not outlive, so async views use the sync client too.
- **A script** ([plans_to_csv.py](https://github.com/haau3/haau3-python/blob/main/examples/plans_to_csv.py)):
  every page of a plan search, written to CSV, with the client as a context manager.

## Testing your code

Pass an `httpx2.Client` with a `MockTransport`, and your code calls the SDK as it always does:

```python
import httpx2

from haau3 import Haau3


def answer(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json={"resourceType": "InsurancePlan", "id": "H2406-013-000"})


test_client = Haau3(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(answer)))
assert test_client.read_insurance_plan("H2406-013-000").get("id") == "H2406-013-000"
```

To run code you have not changed against a stand-in API of your own, set `HAAU3_BASE_URL` to its
address: a client built with no `base_url` sends there, and nowhere else.

## Versions

The SDK is `0.y.z` while the API is in preview: a minor release may change what a method takes or
answers, and a patch release never does. [CHANGELOG.md](https://github.com/haau3/haau3-python/blob/main/CHANGELOG.md)
lists every release, and a release that takes a new version of the API's spec names that version;
`haau3.SPEC_VERSION` is the one a release was generated from.

## License

[MIT](https://github.com/haau3/haau3-python/blob/main/LICENSE)
