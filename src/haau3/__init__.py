"""The Python SDK for the haau3 platform API (api.haau3.com).

```python
from haau3 import Haau3

client = Haau3()  # reads HAAU3_API_KEY
plan = client.read_insurance_plan("H2406-013-000")
```

`Haau3` is the client for code that calls and waits, `AsyncHaau3` for code that awaits. Every
operation of the API is a method on both, named by its operationId in snake_case.
"""

from . import calls as calls
from . import fhir as fhir
from . import types as types
from ._batch import BatchError as BatchError
from ._batch import BatchOk as BatchOk
from ._batch import BatchResult as BatchResult
from ._batch import Call as Call
from ._client import DEFAULT_BASE_URL as DEFAULT_BASE_URL
from ._client import DEFAULT_MAX_RETRIES as DEFAULT_MAX_RETRIES
from ._client import DEFAULT_TIMEOUT as DEFAULT_TIMEOUT
from ._client import AsyncHaau3 as AsyncHaau3
from ._client import Haau3 as Haau3
from ._encode import QueryValue as QueryValue
from ._errors import AuthError as AuthError
from ._errors import ErrorDict as ErrorDict
from ._errors import ForbiddenError as ForbiddenError
from ._errors import Haau3Error as Haau3Error
from ._errors import NoAnswerError as NoAnswerError
from ._errors import NoAnswerReason as NoAnswerReason
from ._errors import RefusedError as RefusedError
from ._methods import AsyncWithResponse as AsyncWithResponse
from ._methods import SyncWithResponse as SyncWithResponse
from ._operations import SPEC_VERSION as SPEC_VERSION
from ._response import RawResponse as RawResponse
from ._response import Response as Response
from ._version import __version__ as __version__

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT",
    "SPEC_VERSION",
    "AsyncHaau3",
    "AsyncWithResponse",
    "AuthError",
    "BatchError",
    "BatchOk",
    "BatchResult",
    "Call",
    "ErrorDict",
    "ForbiddenError",
    "Haau3",
    "Haau3Error",
    "NoAnswerError",
    "NoAnswerReason",
    "QueryValue",
    "RawResponse",
    "RefusedError",
    "Response",
    "SyncWithResponse",
    "__version__",
    "calls",
    "fhir",
    "types",
]
