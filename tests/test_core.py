import json
from pathlib import Path

import pytest

import vrgb

DEVICES = sorted(vrgb.SUPPORTED_DEVICES.items())
REQUIRED_FIELDS = {"hid_name", "model", "firmware_report_id", "color_report_id"}


@pytest.fixture
def sent(monkeypatch):
    """Capture HID feature reports instead of writing to /dev/hidraw*."""
    reports = []
    monkeypatch.setattr(
        vrgb, "hid_set_feature", lambda path, rid, payload: reports.append((path, rid, payload))
    )
    return reports


@pytest.fixture
def config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(vrgb, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(vrgb, "CONFIG_FILE", tmp_path / "config.json")
    return tmp_path


def devinfo(hid_id):
    return {"path": "/dev/hidraw9", **vrgb.SUPPORTED_DEVICES[hid_id]}


# ===== Device table =====


@pytest.mark.parametrize("hid_id,dev", DEVICES)
def test_device_entry_is_complete(hid_id, dev):
    assert REQUIRED_FIELDS <= dev.keys()
    assert 0 <= dev["firmware_report_id"] <= 0xFF
    assert 0 <= dev["color_report_id"] <= 0xFF
    assert dev["firmware_report_id"] != dev["color_report_id"]
    _, vendor, product = hid_id.split(":")
    name_vendor, name_product = dev["hid_name"].split()[-1].split(":")
    assert (int(vendor, 16), int(product, 16)) == (int(name_vendor, 16), int(name_product, 16))


# ===== Report bytes =====


@pytest.mark.parametrize("hid_id,dev", DEVICES)
def test_set_color_uses_device_report_id(sent, hid_id, dev):
    vrgb.set_color(devinfo(hid_id), 0x12, 0x34, 0x56, 0x78)
    assert sent == [("/dev/hidraw9", dev["color_report_id"], bytes.fromhex("010000000012345678"))]


@pytest.mark.parametrize("hid_id,dev", DEVICES)
@pytest.mark.parametrize("enabled,byte", [(True, vrgb.FIRMWARE_BYTE), (False, vrgb.HOST_BYTE)])
def test_set_firmware_mode(sent, hid_id, dev, enabled, byte):
    vrgb.set_firmware_mode(devinfo(hid_id), enabled)
    assert sent == [("/dev/hidraw9", dev["firmware_report_id"], bytes([byte]))]


# Pinned bytes for hardware verified on real devices: these must never change by accident.
@pytest.mark.parametrize(
    "hid_id,color_report",
    [
        ("0018:00000B05:000019B6", "05010000000000aaffff"),
        ("0018:00000B05:00005570", "45010000000000aaffff"),
    ],
)
def test_verified_device_bytes(sent, hid_id, color_report):
    vrgb.set_color(devinfo(hid_id), 0x00, 0xAA, 0xFF, 255)
    _, rid, payload = sent[0]
    assert (bytes([rid]) + payload).hex() == color_report


def test_set_color_clamps(sent):
    vrgb.set_color(devinfo(DEVICES[0][0]), -5, 300, 128, 999)
    assert sent[0][2][-4:] == bytes([0, 255, 128, 255])


def test_ioctl_request_encodes_length():
    assert vrgb.HIDIOCSFEATURE(10) == 0xC00A4806


# ===== Helpers =====


@pytest.mark.parametrize("text,rgb", [("00aaff", (0, 170, 255)), ("#FFFFFF", (255, 255, 255)), (" 010203 ", (1, 2, 3))])
def test_hex_to_rgb(text, rgb):
    assert vrgb.hex_to_rgb(text) == rgb


@pytest.mark.parametrize("text", ["fff", "gg0000", ""])
def test_hex_to_rgb_rejects(text):
    with pytest.raises(SystemExit):
        vrgb.hex_to_rgb(text)


@pytest.mark.parametrize("percent,intensity", [(0, 0), (50, 128), (100, 255), (150, 255), (-1, 0)])
def test_percent_to_intensity(percent, intensity):
    assert vrgb.percent_to_intensity(percent) == intensity


# ===== Device discovery =====


def fake_hidraw(root, entries, descriptors=None):
    for name, uevent in entries.items():
        (root / name / "device").mkdir(parents=True)
        (root / name / "device" / "uevent").write_text(uevent)
    for name, descriptor in (descriptors or {}).items():
        (root / name / "device" / "report_descriptor").write_bytes(descriptor)


@pytest.fixture
def hidraw(monkeypatch, tmp_path):
    real_path = vrgb.Path
    monkeypatch.setattr(vrgb, "Path", lambda p: tmp_path if p == "/sys/class/hidraw" else real_path(p))
    monkeypatch.setattr(vrgb, "module_loaded", lambda name: True)
    return tmp_path


def test_find_device_prefers_exact_hid_id(hidraw):
    fake_hidraw(hidraw, {
        "hidraw0": "HID_ID=0003:0000046D:0000C52B\nHID_NAME=Logitech Receiver\n",
        "hidraw1": "HID_ID=0018:00000B05:00005570\nHID_NAME=ITE5570:00 0B05:5570\n",
    })
    dev = vrgb.find_device()
    assert dev["path"] == "/dev/hidraw1"
    assert dev["color_report_id"] == 0x45


def test_find_device_falls_back_to_hid_name(hidraw):
    fake_hidraw(hidraw, {"hidraw0": "HID_ID=0018:00000B05:0000FFFF\nHID_NAME=ITE5570:00 0B05:19B6\n"})
    assert vrgb.find_device()["color_report_id"] == 0x05


def test_find_device_none(hidraw):
    fake_hidraw(hidraw, {"hidraw0": "HID_ID=0003:0000046D:0000C52B\nHID_NAME=Mouse\n"})
    with pytest.raises(SystemExit):
        vrgb.find_device()


ITE5570_19B6_DESCRIPTOR = (Path(__file__).parent / "data" / "ite5570_19b6.desc").read_bytes()


def test_parse_real_ite5570_descriptor():
    ids = vrgb.parse_lamparray_report_ids(ITE5570_19B6_DESCRIPTOR)
    assert ids[vrgb.LAMPARRAY_ATTRIBUTES_REPORT] == 0x01
    assert ids[vrgb.LAMPARRAY_RANGE_UPDATE_REPORT] == 0x05
    assert ids[vrgb.LAMPARRAY_CONTROL_REPORT] == 0x0B


def test_parse_report_id_inside_collection():
    descriptor = bytes([
        0x05, 0x59,        # Usage Page (LampArray)
        0x09, 0x01,        # Usage (LampArray)
        0xA1, 0x01,        # Collection (Application)
        0x09, 0x60,        #   Usage (LampRangeUpdateReport)
        0xA1, 0x02,        #   Collection (Logical)
        0x85, 0x45,        #     Report ID (0x45)
        0x09, 0x55,        #     Usage (LampUpdateFlags)
        0xB1, 0x02,        #     Feature
        0xC0,              #   End Collection
        0xC0,              # End Collection
    ])
    assert vrgb.parse_lamparray_report_ids(descriptor)[vrgb.LAMPARRAY_RANGE_UPDATE_REPORT] == 0x45


def test_parse_ignores_other_usage_pages():
    descriptor = bytes([0x06, 0x30, 0xFF, 0x09, 0x60, 0xA1, 0x01, 0x85, 0x05, 0xB1, 0x02, 0xC0])
    assert vrgb.parse_lamparray_report_ids(descriptor) == {}


def test_find_device_reads_report_ids_from_descriptor(hidraw):
    fake_hidraw(
        hidraw,
        {"hidraw0": "HID_ID=0018:00000B05:000019B6\nHID_NAME=ITE5570:00 0B05:19B6\n"},
        {"hidraw0": ITE5570_19B6_DESCRIPTOR},
    )
    dev = vrgb.find_device()
    assert (dev["firmware_report_id"], dev["color_report_id"], dev["verified"]) == (0x0B, 0x05, True)
    assert "lamp_id_end" not in dev


def test_find_device_detects_unlisted_lamparray(hidraw, monkeypatch):
    monkeypatch.setattr(vrgb, "get_lamp_count", lambda devinfo: 4)
    fake_hidraw(
        hidraw,
        {"hidraw0": "HID_ID=0018:00001234:00005678\nHID_NAME=Other Keyboard\n"},
        {"hidraw0": ITE5570_19B6_DESCRIPTOR},
    )
    dev = vrgb.find_device()
    assert dev["verified"] is False
    assert (dev["color_report_id"], dev["lamp_id_end"]) == (0x05, 3)


def test_find_device_prefers_verified_over_generic(hidraw, monkeypatch):
    monkeypatch.setattr(vrgb, "get_lamp_count", lambda devinfo: 1)
    fake_hidraw(
        hidraw,
        {
            "hidraw0": "HID_ID=0018:00001234:00005678\nHID_NAME=Other Keyboard\n",
            "hidraw1": "HID_ID=0018:00000B05:00005570\nHID_NAME=ITE5570:00 0B05:5570\n",
        },
        {"hidraw0": ITE5570_19B6_DESCRIPTOR},
    )
    assert vrgb.find_device()["path"] == "/dev/hidraw1"


def test_set_color_lamp_range(sent):
    vrgb.set_color({"path": "p", "color_report_id": 5, "lamp_id_end": 0x0102}, 1, 2, 3, 4)
    assert sent[0][2] == bytes.fromhex("010000" "0201" "01020304")


# ===== Config =====


def test_load_config_defaults_when_missing(config_dir):
    assert vrgb.load_config() == vrgb.default_config()


def test_load_config_backs_up_corrupt_file(config_dir):
    (config_dir / "config.json").write_text("{not json")
    assert vrgb.load_config() == vrgb.default_config()
    assert (config_dir / "config.json.bad").exists()


def test_load_config_normalizes_values(config_dir):
    (config_dir / "config.json").write_text(json.dumps({
        "color": "#ZZZZZZ", "percent": 500, "profiles": {"a": {"color": "ABCDEF", "percent": "x"}, "b": 3},
    }))
    cfg = vrgb.load_config()
    assert cfg["color"] == "aa00ff"
    assert cfg["percent"] == 100
    assert cfg["profiles"] == {"a": {"color": "abcdef", "percent": 100, "autonomous": False}}


def test_save_config_keeps_unknown_keys(config_dir):
    cfg = {**vrgb.default_config(), "idle_enabled": False}
    vrgb.save_config(cfg)
    assert vrgb.load_config()["idle_enabled"] is False


def test_get_real_home_honours_sudo_user(monkeypatch):
    import pwd
    me = pwd.getpwuid(__import__("os").getuid())
    monkeypatch.setenv("SUDO_USER", me.pw_name)
    assert str(vrgb.get_real_home()) == me.pw_dir


def test_get_real_home_honours_pkexec_uid(monkeypatch):
    import os
    import pwd
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setenv("PKEXEC_UID", str(os.getuid()))
    assert str(vrgb.get_real_home()) == pwd.getpwuid(os.getuid()).pw_dir


# ===== Commands =====


def test_cmd_set_writes_device_and_config(sent, config_dir):
    cfg = vrgb.default_config()
    vrgb.cmd_set(cfg, devinfo(DEVICES[0][0]), "#00AA55", 50)
    assert [p for _, _, p in sent] == [bytes([vrgb.HOST_BYTE]), bytes.fromhex("0100000000" "00aa55" "80")]
    saved = vrgb.load_config()
    assert (saved["color"], saved["percent"], saved["last_on_percent"]) == ("00aa55", 50, 50)


def test_profile_roundtrip(sent, config_dir, capsys):
    cfg = vrgb.default_config()
    vrgb.cmd_set(cfg, devinfo(DEVICES[0][0]), "123456", 40)
    vrgb.cmd_profile_save(cfg, " work ")
    vrgb.cmd_set(cfg, devinfo(DEVICES[0][0]), "ffffff", 100)
    sent.clear()
    vrgb.cmd_profile_load(cfg, devinfo(DEVICES[0][0]), "work")
    assert sent[-1][2] == bytes.fromhex("0100000000" "123456" "66")
    vrgb.cmd_profile_delete(cfg, "work")
    assert vrgb.load_config()["profiles"] == {}
