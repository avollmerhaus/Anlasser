import json
from jsonschema import Draft202012Validator, ValidationError

from .errors import (
    AnlasserInvalidActionError,
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

    "required": ["action"],
    "additionalProperties": False,

    "allOf": [
        {
            "if": {
                "properties": {
                    "action": {"enum": ["get_vm_state", "set_vm_state"]},
                }
            },
            "then": {
                "required": ["vm_name"],
            },
        },
        {
            "if": {
                "properties": {
                    "action": {"const": "set_vm_state"},
                }
            },
            "then": {
                "required": ["state"],
            },
        },
    ],
}

ANLASSER_ERROR_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "enum": ["invalid_request", "vm_error", "internal_error"],
        },
        "message": {"type": "string", "minLength": 1},
    },
    "required": ["code", "message"],
    "additionalProperties": False,
}

ANLASSER_RESPONSE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "result": {},
        "error": ANLASSER_ERROR_SCHEMA,
    },
    "required": ["success"],
    "additionalProperties": False,
    "allOf": [
        {
            "if": {"properties": {"success": {"const": False}}},
            "then": {"required": ["error"]},
        },
        {
            "if": {"properties": {"success": {"const": True}}},
            "then": {"not": {"required": ["error"]}},
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
        msg = str(e).splitlines()[0]
        raise AnlasserInvalidMessageError(f"Invalid request: {msg}") from e

    return data

# Why validate our own responses?
# Just never make a mistake, d'uh! ;)
def validate_anlasser_response(response):
    try:
        _response_validator.validate(response)
    except ValidationError as e:
        raise AnlasserInvalidResponseError("Invalid response") from e
