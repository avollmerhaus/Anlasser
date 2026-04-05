import json
from jsonschema import Draft202012Validator, ValidationError

from .errors import (
    AnlasserInvalidMessageError,
    AnlasserInvalidResponseError,
)

ANLASSER_REQUEST_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list_vms", "get_vm_state", "set_vm_state"],
        },
        "body": {
            "type": "object",
            "properties": {
                "vm_name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 255,
                    "pattern": r"^[\x00-\x7F]+$",
                },
                "state": {
                    "type": "string",
                    "enum": ["up", "down"],
                },
            },
            "additionalProperties": False,
        },
    },
    "required": ["action", "body"],
    "additionalProperties": False,
    "allOf": [
        {
            "if": {
                "properties": {
                    "action": {"enum": ["get_vm_state", "set_vm_state"]},
                }
            },
            "then": {
                "properties": {"body": {"required": ["vm_name"]}},
            },
        },
        {
            "if": {
                "properties": {
                    "action": {"const": "set_vm_state"},
                }
            },
            "then": {
                "properties": {"body": {"required": ["state"]}},
            },
        },
    ],
}

ANLASSER_RESPONSE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "status": {"type": "integer", "minimum": 100, "maximum": 599},
        "body": {
            "type": "object",
            "properties": {
                "response": {},
                "error": {"type": "string", "minLength": 1},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "required": ["status", "body"],
    "additionalProperties": False,
    "allOf": [
        {
            "if": {"properties": {"status": {"minimum": 200, "maximum": 299}}},
            "then": {"properties": {"body": {"required": ["response"]}}},
            "else": {"properties": {"body": {"required": ["error"]}}},
        },
    ],
}

# The validators don't change, let's keep them initialized instead of
# recreating it on every request from inside the function.
_request_validator = Draft202012Validator(ANLASSER_REQUEST_SCHEMA)
_response_validator = Draft202012Validator(ANLASSER_RESPONSE_SCHEMA)


def parse_anlasser_request(client_msg):
    try:
        data = json.loads(client_msg)
        _request_validator.validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        raise AnlasserInvalidMessageError(f"Invalid request: {e}") from e

    return data


# Why validate our own responses?
# Just never make a mistake, d'uh! ;)
def validate_anlasser_response(response):
    try:
        _response_validator.validate(response)
    except ValidationError as e:
        raise AnlasserInvalidResponseError(f"Invalid response: {e}") from e
