from n3x_bot.gates import build_gate_content, build_gate_embed, parse_gate_message


# ── parse_gate_message ──────────────────────────────────────────────────────

def test_parse_gate_message_valid_lowercase():
    assert parse_gate_message("a 46892") == ("a", 46892)


def test_parse_gate_message_valid_uppercase_gate_type_normalizes_to_lower():
    assert parse_gate_message("A 46892") == ("a", 46892)


def test_parse_gate_message_strips_german_dotted_thousands_separators():
    assert parse_gate_message("A 1.234.567") == ("a", 1234567)


def test_parse_gate_message_accepts_b_and_c():
    assert parse_gate_message("b 100") == ("b", 100)
    assert parse_gate_message("c 200") == ("c", 200)


def test_parse_gate_message_invalid_gate_type_returns_none():
    assert parse_gate_message("d 100") is None


def test_parse_gate_message_missing_cost_returns_none():
    assert parse_gate_message("a") is None


def test_parse_gate_message_non_numeric_cost_returns_none():
    assert parse_gate_message("a abc") is None


def test_parse_gate_message_extra_trailing_text_returns_none():
    assert parse_gate_message("a 100 extra") is None


def test_parse_gate_message_dots_only_cost_returns_none():
    assert parse_gate_message("a ...") is None


def test_parse_gate_message_tolerates_surrounding_whitespace():
    assert parse_gate_message("  a 100  ") == ("a", 100)


def test_parse_gate_message_ignores_command_prefixed_text():
    assert parse_gate_message("!stat a") is None


# ── build_gate_content (totals -> content math) ─────────────────────────────

def test_build_gate_content_computes_avg_and_positive_diff():
    totals = {"a": {"count": 2, "avg": 46500}}
    rewards = {"a": 46892, "b": 93820, "c": 139522}

    content = build_gate_content(totals, rewards)

    assert "Alpha Gate" in content
    assert "Total Gates: 2" in content
    assert "46.500" in content  # format_number uses "." as thousands sep
    assert "🟢" in content  # reward (46892) - avg (46500) = +392 -> green
    assert "392" in content


def test_build_gate_content_negative_diff_uses_red_indicator():
    totals = {"a": {"count": 1, "avg": 50000}}
    rewards = {"a": 46892, "b": 93820, "c": 139522}

    content = build_gate_content(totals, rewards)

    assert "🔴" in content
    assert "-3.108" in content  # 46892 - 50000 = -3108


def test_build_gate_content_zero_count_defaults_diff_to_zero():
    totals = {}
    rewards = {"a": 46892, "b": 93820, "c": 139522}

    content = build_gate_content(totals, rewards)

    assert "Total Gates: 0" in content
    # zero count -> diff forced to 0 regardless of reward, per v2 semantics
    assert content.count("🟢") == 3


def test_build_gate_content_includes_all_three_gate_types_in_order():
    totals = {}
    rewards = {"a": 46892, "b": 93820, "c": 139522}

    content = build_gate_content(totals, rewards)

    assert content.index("Alpha Gate") < content.index("Beta Gate") < content.index("Gamma Gate")


# ── build_gate_embed ─────────────────────────────────────────────────────────

def test_build_gate_embed_sets_title_description_and_footer():
    totals = {"a": {"count": 2, "avg": 46500}}
    rewards = {"a": 46892, "b": 93820, "c": 139522}

    embed = build_gate_embed(totals, rewards, "05.07.2026 12:00")

    assert embed.title == "📊 Gate Statistics"
    assert "Alpha Gate" in embed.description
    assert embed.footer.text == "Last Update: 05.07.2026 12:00"
