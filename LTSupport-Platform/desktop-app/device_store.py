import json
import os
import uuid


def _config_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "VantagePoint")
    os.makedirs(path, exist_ok=True)
    return path


def _config_path():
    return os.path.join(_config_dir(), "device.json")


def _load():
    try:
        with open(_config_path(), "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data):
    try:
        with open(_config_path(), "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def load_device_id(org_id=None):
    """Returns the cached device_id, but ONLY if it was last saved under the same
    account. Without this, testing/switching between accounts (or even just re-signing
    up) on one machine would keep reusing a stale id -- which the relay then rejects
    with DEVICE_ID_TAKEN if that id is now owned by a different account, or silently
    carries over a confusing name (e.g. a Local Mode fallback id) if it happens to still
    be owned by the same one."""
    data = _load()
    cached_org = data.get("device_id_org")
    if org_id is not None and cached_org is not None and cached_org != org_id:
        return ""
    return data.get("device_id", "")


def save_device_id(device_id, org_id=None):
    data = _load()
    data["device_id"] = device_id
    if org_id is not None:
        data["device_id_org"] = org_id
    _save(data)


def load_machine_id():
    """A random id generated once per installation (independent of account/device_id),
    used only to detect a viewer connecting from the same physical machine it's hosting
    from -- not a security credential, just a same-machine hint."""
    data = _load()
    machine_id = data.get("machine_id")
    if not machine_id:
        machine_id = uuid.uuid4().hex
        data["machine_id"] = machine_id
        _save(data)
    return machine_id
