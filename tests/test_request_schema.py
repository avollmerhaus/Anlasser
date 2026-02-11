import json

import pytest

from anlasser.messages import parse_anlasser_request
from anlasser.errors import AnlasserInvalidMessageError


def test_anlasser_request_valid_variants():
    parse_anlasser_request(json.dumps({"action": "list_vms"}))
    parse_anlasser_request(json.dumps({"action": "get_vm_state", "vm_name": "testvm1"}))
    parse_anlasser_request(
        json.dumps({"action": "set_vm_state", "vm_name": "testvm1", "state": "up"})
    )


def test_anlasser_request_invalid_payloads():
    with pytest.raises(AnlasserInvalidMessageError):
        parse_anlasser_request(json.dumps({"action": "nope"}))

    with pytest.raises(AnlasserInvalidMessageError):
        parse_anlasser_request(json.dumps({"action": "get_vm_state"}))

    with pytest.raises(AnlasserInvalidMessageError):
        parse_anlasser_request(
            json.dumps(
                {"action": "set_vm_state", "vm_name": "testvm1", "state": "maybe"}
            )
        )

    with pytest.raises(AnlasserInvalidMessageError):
        parse_anlasser_request(json.dumps({"action": "list_vms", "extra": True}))
