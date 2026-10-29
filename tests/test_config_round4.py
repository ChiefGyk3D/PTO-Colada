"""Config loader, round 4: no bad scalar, tag or position may escape as a traceback."""

from __future__ import annotations

import itertools

import pytest

from pto_colada.cli import main
from pto_colada.config import build_config, load_config_file

yaml = pytest.importorskip("yaml")

TAGS = ["str", "int", "float", "bool", "null", "timestamp", "seq", "map", "binary", "set",
        "omap", "pairs", "merge", "value", "yaml"]
NODES = {"scalar": "bad", "sequence": "[bad]", "mapping": "{bad: bad}"}
POSITIONS = ["value", "key"]


def document(tag, node, position):
    if position == "value":
        return "name: !!%s %s\nholidays: [{name: X, date: 2027-03-03}]\n" % (tag, node)
    return "? !!%s %s\n: 1\nholidays: [{name: X, date: 2027-03-03}]\n" % (tag, node)


@pytest.mark.parametrize(
    "tag,kind,position", list(itertools.product(TAGS, NODES, POSITIONS))
)
def test_every_standard_tag_loads_or_fails_cleanly(tmp_path, capsys, tag, kind, position):
    path = tmp_path / "c.yaml"
    path.write_text(document(tag, NODES[kind], position))
    try:
        load_config_file(str(path))
    except ValueError as exc:
        assert "Traceback" not in str(exc)
    # the same file through the real command: success or exit 2, nothing else
    try:
        code = main(["--config", str(path), "--start-year", "2027", "--years", "1"])
    except SystemExit as exc:
        code = exc.code
        assert code == 2
        assert "Traceback" not in capsys.readouterr().err
    else:
        assert code == 0


def test_the_reported_bool_case(tmp_path, capsys):
    path = tmp_path / "c.yaml"
    path.write_text("name: !!bool not-a-bool\nholidays: [{name: X, date: 2027-03-03}]\n")
    with pytest.raises(ValueError) as exc:
        build_config(config_path=str(path))
    message = str(exc.value)
    assert "invalid YAML" in message and "line 1" in message and "!!bool" in message
    assert "not-a-bool" not in message
    with pytest.raises(SystemExit) as raised:
        main(["--config", str(path), "--start-year", "2027", "--years", "1"])
    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and "not-a-bool" not in err


def test_unexpected_errors_in_the_yaml_constructors_become_clean_errors(tmp_path, monkeypatch):
    def explode(loader, node):
        raise AttributeError("internal detail secret-token")

    monkeypatch.setitem(yaml.SafeLoader.yaml_constructors, "tag:yaml.org,2002:str", explode)
    path = tmp_path / "c.yaml"
    path.write_text("name: hello\n")
    with pytest.raises(ValueError) as exc:
        load_config_file(str(path))
    assert "invalid YAML" in str(exc.value) and "secret-token" not in str(exc.value)


def test_unexpected_errors_in_the_json_parse_become_clean_errors(tmp_path, monkeypatch):
    import pto_colada.config as config_module

    def explode(path):
        def hook(pairs):
            raise KeyError("internal detail secret-token")
        return hook

    monkeypatch.setattr(config_module, "_json_object", explode)
    path = tmp_path / "c.json"
    path.write_text('{"name": "x"}')
    with pytest.raises(ValueError) as exc:
        load_config_file(str(path))
    assert "not valid JSON" in str(exc.value) and "secret-token" not in str(exc.value)
