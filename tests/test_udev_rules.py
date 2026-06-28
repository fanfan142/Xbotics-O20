import pytest

from xbotics_o20.udev_rules import UsbRuleTarget, build_usb_udev_rule, normalize_usb_id


def test_normalize_usb_id_accepts_prefixed_hex() -> None:
    assert normalize_usb_id("0xA8FA") == "a8fa"


def test_normalize_usb_id_rejects_bad_value() -> None:
    with pytest.raises(ValueError):
        normalize_usb_id("usb0")


def test_build_usb_udev_rule_defaults_to_permission_only() -> None:
    rule = build_usb_udev_rule(UsbRuleTarget("a8fa", "8598", serial="ABC"))

    assert 'ATTRS{idVendor}=="a8fa"' in rule
    assert 'ATTRS{idProduct}=="8598"' in rule
    assert 'MODE="0666"' in rule
    assert "serial" not in rule
    assert "SYMLINK" not in rule
