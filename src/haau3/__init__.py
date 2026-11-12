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
from ._app_info import AppInfo as AppInfo
from ._batch import BatchError as BatchError
from ._batch import BatchOk as BatchOk
from ._batch import BatchResult as BatchResult
from ._batch import Call as Call
from ._client import DEFAULT_BASE_URL as DEFAULT_BASE_URL
from ._client import DEFAULT_MAX_RETRIES as DEFAULT_MAX_RETRIES
from ._client import DEFAULT_TIMEOUT as DEFAULT_TIMEOUT
from ._client import AsyncHaau3 as AsyncHaau3
from ._client import Environment as Environment
from ._client import Haau3 as Haau3
from ._encode import QueryValue as QueryValue
from ._errors import AuthError as AuthError
from ._errors import ClientErrorCode as ClientErrorCode
from ._errors import ErrorDict as ErrorDict
from ._errors import ForbiddenError as ForbiddenError
from ._errors import Haau3Error as Haau3Error
from ._errors import NoAnswerError as NoAnswerError
from ._errors import NoAnswerReason as NoAnswerReason
from ._errors import RefusedError as RefusedError
from ._methods import AsyncWithResponse as AsyncWithResponse
from ._methods import SyncWithResponse as SyncWithResponse
from ._operations import SPEC_VERSION as SPEC_VERSION
from ._parameters import NDC_SYSTEM as NDC_SYSTEM
from ._parameters import RXNORM_SYSTEM as RXNORM_SYSTEM
from ._parameters import DosageReview as DosageReview
from ._parameters import ReviewFlag as ReviewFlag
from ._parameters import parameter as parameter
from ._parameters import parameters as parameters
from ._parameters import review_flags as review_flags
from ._parameters import structure_dosage_request as structure_dosage_request
from ._response import RawResponse as RawResponse
from ._response import Response as Response
from ._terminology import PREFERRED_FOR_LANGUAGE_USE as PREFERRED_FOR_LANGUAGE_USE
from ._terminology import SYNONYM_USE as SYNONYM_USE
from ._terminology import BooleanProperty as BooleanProperty
from ._terminology import CodingProperty as CodingProperty
from ._terminology import DecimalProperty as DecimalProperty
from ._terminology import Designation as Designation
from ._terminology import DesignationUse as DesignationUse
from ._terminology import IntegerProperty as IntegerProperty
from ._terminology import LookupResult as LookupResult
from ._terminology import Property as Property
from ._terminology import TextProperty as TextProperty
from ._terminology import ValidationResult as ValidationResult
from ._terminology import designations as designations
from ._terminology import lookup_result as lookup_result
from ._terminology import validation_result as validation_result
from ._test_data import mark_as_test_data as mark_as_test_data
from ._version import __version__ as __version__

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT",
    "NDC_SYSTEM",
    "PREFERRED_FOR_LANGUAGE_USE",
    "RXNORM_SYSTEM",
    "SPEC_VERSION",
    "SYNONYM_USE",
    "AppInfo",
    "AsyncHaau3",
    "AsyncWithResponse",
    "AuthError",
    "BatchError",
    "BatchOk",
    "BatchResult",
    "BooleanProperty",
    "Call",
    "ClientErrorCode",
    "CodingProperty",
    "DecimalProperty",
    "Designation",
    "DesignationUse",
    "Environment",
    "ErrorDict",
    "ForbiddenError",
    "Haau3",
    "Haau3Error",
    "IntegerProperty",
    "LookupResult",
    "NoAnswerError",
    "NoAnswerReason",
    "Property",
    "QueryValue",
    "RawResponse",
    "RefusedError",
    "Response",
    "SyncWithResponse",
    "TextProperty",
    "ValidationResult",
    "__version__",
    "calls",
    "designations",
    "fhir",
    "lookup_result",
    "mark_as_test_data",
    "parameter",
    "parameters",
    "review_flags",
    "structure_dosage_request",
    "types",
    "validation_result",
]
