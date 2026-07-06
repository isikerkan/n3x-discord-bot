from n3x_bot.format import format_number


def test_format_number_german_grouping():
    assert format_number(1234567) == "1.234.567"
    assert format_number(0) == "0"
    assert format_number(999) == "999"
