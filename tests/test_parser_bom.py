from importlib.resources import files

from nullone.sources.parsers import parse_wngsr_json


def test_parse_json_with_bom():
    raw = files("nullone.fixtures").joinpath("wngsr_sample.json").read_bytes()
    reading = parse_wngsr_json(b"\xef\xbb\xbf" + raw)
    assert reading.net_change_bcf == 101
