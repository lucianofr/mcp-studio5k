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
