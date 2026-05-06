import pytest

from helium_edm.cli import parse_sendy_json


def test_parse_sendy_json_accepts_sendy_control_characters():
    payload = '{"list1":{"id":"abc","name":"\tImported list"}}'

    assert parse_sendy_json(payload) == {"list1": {"id": "abc", "name": "\tImported list"}}


def test_parse_sendy_json_rejects_errors():
    with pytest.raises(ValueError, match="Error:"):
        parse_sendy_json("Error: invalid API key")
