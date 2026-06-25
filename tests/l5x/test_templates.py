import pytest

from mcp_studio5k.l5x.templates import get_l5x_template


@pytest.mark.parametrize("kind", ["st", "ld", "fbd"])
def test_template_returns_routine_of_expected_type(kind):
    text = get_l5x_template(kind)
    assert "<RSLogix5000Content" in text
    assert "<Routine" in text


def test_template_st_has_stcontent():
    assert "<STContent>" in get_l5x_template("st")


def test_template_ld_has_rllcontent():
    assert "<RLLContent>" in get_l5x_template("ld")


def test_template_fbd_has_fbdcontent_sheet():
    text = get_l5x_template("fbd")
    assert "<FBDContent" in text
    assert 'SheetSize="' in text
    assert 'SheetOrientation="' in text
    assert "<Sheet" in text


def test_unknown_kind_raises_value_error():
    with pytest.raises(ValueError, match="unknown template kind"):
        get_l5x_template("scl")


from mcp_studio5k.l5x.validate import validate_l5x


@pytest.mark.parametrize("kind", ["st", "ld", "fbd"])
def test_template_passes_validate_l5x(kind):
    result = validate_l5x(get_l5x_template(kind))
    assert result.ok, [(i.severity, i.message) for i in result.issues]


def test_fbd_template_is_parseable_and_round_trips():
    from mcp_studio5k.l5x.parse import parse_l5x

    text = get_l5x_template("fbd")
    root = parse_l5x(text.encode("utf-8"))
    routine = root.find(".//Routine[@Type='FBD']")
    assert routine is not None
    sheet = routine.find(".//Sheet")
    tags = [el.tag for el in sheet]
    assert tags.count("Wire") == 2
    assert "Block" in tags and "IRef" in tags and "OCon" in tags
