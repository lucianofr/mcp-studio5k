from mcp_studio5k.l5x.parse import parse_l5x, routine_type


def test_st_fixture_parses_as_st(st_gearchange_l5x):
    root = parse_l5x(st_gearchange_l5x, max_bytes=5_000_000)
    assert routine_type(root) == "ST"
    lines = root.findall(".//STContent/Line")
    assert len(lines) == 5


def test_ld_fixture_parses_as_rll(ld_scale_value_l5x):
    root = parse_l5x(ld_scale_value_l5x, max_bytes=5_000_000)
    assert routine_type(root) == "RLL"
    rungs = root.findall(".//RLLContent/Rung")
    assert len(rungs) == 2
