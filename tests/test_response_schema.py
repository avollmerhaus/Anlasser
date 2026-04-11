import pytest

from anlasser.errors import AnlasserInvalidResponseError
from anlasser.messages import validate_anlasser_response


# Intention: verify that all valid response shapes pass schema validation.
# Expected outcome: no exception raised for success and error responses.
def test_anlasser_response_valid_variants():
    validate_anlasser_response({"status": 200, "body": {"response": {"vm_list": []}}})
    validate_anlasser_response({"status": 201, "body": {"response": "ok"}})
    validate_anlasser_response({"status": 400, "body": {"error": "invalid request"}})
    validate_anlasser_response({"status": 500, "body": {"error": "internal error"}})


# Intention: verify that malformed responses are rejected by schema validation.
# Expected outcome: AnlasserInvalidResponseError for each invalid variant.
def test_anlasser_response_invalid_payloads():
    with pytest.raises(AnlasserInvalidResponseError):
        validate_anlasser_response({"status": 200, "body": {}})

    with pytest.raises(AnlasserInvalidResponseError):
        validate_anlasser_response({"status": 400, "body": {}})

    with pytest.raises(AnlasserInvalidResponseError):
        validate_anlasser_response({"status": "200", "body": {"response": "ok"}})

    with pytest.raises(AnlasserInvalidResponseError):
        validate_anlasser_response({"status": 500, "body": {"response": "ok"}})

    with pytest.raises(AnlasserInvalidResponseError):
        validate_anlasser_response({"status": 200, "body": {"error": "nope"}})
