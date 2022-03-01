import configparser
import datetime
import os

import pytest
from pytimeparse.timeparse import timeparse

from flytekit.configuration import set_if_exists, get_config_file, ConfigEntry
from flytekit.configuration.file import LegacyConfigEntry


def test_set_if_exists():
    d = {}
    d = set_if_exists(d, "k", None)
    assert len(d) == 0
    d = set_if_exists(d, "k", [])
    assert len(d) == 0
    d = set_if_exists(d, "k", "x")
    assert len(d) == 1
    assert d["k"] == "x"


def test_get_config_file():
    c = get_config_file(None)
    assert c is None
    c = get_config_file(os.path.join(os.path.dirname(os.path.realpath(__file__)), "configs/good.config"))
    assert c is not None
    assert c.legacy_config is not None

    with pytest.raises(configparser.Error):
        get_config_file(os.path.join(os.path.dirname(os.path.realpath(__file__)), "configs/bad.config"))


def test_config_entry_envvar():
    # Pytest feature
    c = ConfigEntry(LegacyConfigEntry("test", "op1", str))
    assert c.read() is None

    old_environ = dict(os.environ)
    os.environ["FLYTE_TEST_OP1"] = "xyz"
    assert c.read() == "xyz"
    os.environ = old_environ


def test_config_entry_file():
    # Pytest feature
    c = ConfigEntry(LegacyConfigEntry("platform", "url", str))
    assert c.read() is None

    cfg = get_config_file(os.path.join(os.path.dirname(os.path.realpath(__file__)), "configs/good.config"))
    assert c.read(cfg) == "fakeflyte.com"

    c = ConfigEntry(LegacyConfigEntry("platform", "url2", str))  # Does not exist
    assert c.read(cfg) is None


def test_config_entry_precedence():
    # Pytest feature
    c = ConfigEntry(LegacyConfigEntry("platform", "url", str))
    assert c.read() is None

    old_environ = dict(os.environ)
    os.environ["FLYTE_PLATFORM_URL"] = "xyz"
    cfg = get_config_file(os.path.join(os.path.dirname(os.path.realpath(__file__)), "configs/good.config"))
    assert c.read(cfg) == "xyz"
    # reset
    os.environ = old_environ


def test_config_entry_types():
    cfg = get_config_file(os.path.join(os.path.dirname(os.path.realpath(__file__)), "configs/good.config"))

    l = ConfigEntry(LegacyConfigEntry("sdk", "workflow_packages", list))
    assert l.read(cfg) == ["this.module", "that.module"]

    s = ConfigEntry(LegacyConfigEntry("madeup", "string_value"))
    assert s.read(cfg) == "abc"

    i = ConfigEntry(LegacyConfigEntry("madeup", "int_value", int))
    assert i.read(cfg) == 3

    b = ConfigEntry(LegacyConfigEntry("madeup", "bool_value", bool))
    assert b.read(cfg) is False

    t = ConfigEntry(LegacyConfigEntry("madeup", "timedelta_value", datetime.timedelta),
                    transform=lambda x: datetime.timedelta(seconds=timeparse(x)))
    assert t.read(cfg) == datetime.timedelta(hours=20)
