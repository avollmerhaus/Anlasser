from types import SimpleNamespace

from .vm import check_vm_name_format

# FIXME: maybe all classes here need __slots__ ?
# Nope. let's replace all this with jsonschema.

class AnlasserRequest(SimpleNamespace):
    
    ACTION_METHODS = {"get_vm_state", "set_vm_state", "list_vms"}

    def __init__(self, action, **kwargs):
        if action not in ACTION_METHODS:
            raise ValueError(f"Invalid action: {action}")
        super().__init__(action=action, **kwargs)


class AnlasserGetVMState(AnlasserRequest):
    def __init__(self, vm_name):
        check_vm_name_format(vm_name)
        super().__init__("get_vm_state", vm_name=vm_name)


class AnlasserSetVMState(AnlasserRequest):
    VALID_STATES = {"up", "down"}
    
    def __init__(self, vm_name, state):
        check_vm_name_format(vm_name)
        if state not in self.VALID_STATES:
            raise ValueError(f"Invalid state: {state}. Must be one of {self.VALID_STATES}")
        super().__init__("set_vm_state", vm_name=vm_name, state=state)


class AnlasserListVMs(AnlasserRequest):
    def __init__(self):
        super().__init__("list_vms")


class AnlasserResponse:
    __slots__ = ("success", "payload")

    def __init__(self, success, payload):
        if not isinstance(success, bool):
            raise TypeError("success must be bool")

        self.success = success
        self.payload = payload
