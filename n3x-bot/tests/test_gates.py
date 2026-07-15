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
    # d/e/z/k are now valid gate types; a genuinely unknown letter is not.
    # (CHANGED from "z 100": z is now the Zeta gate — see report.)
    assert parse_gate_message("f 100") is None


def test_parse_gate_message_delta_is_now_valid():
    assert parse_gate_message("d 100") == ("d", 100)


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


# ── parse_gate_message: Epsilon / Zeta / Kappa (e/z/k) ──────────────────────

def test_parse_gate_message_recognizes_epsilon():
    assert parse_gate_message("e 46892") == ("e", 46892)


def test_parse_gate_message_recognizes_zeta_with_dotted_thousands():
    assert parse_gate_message("Z 1.234") == ("z", 1234)


def test_parse_gate_message_recognizes_kappa():
    assert parse_gate_message("k 500") == ("k", 500)


def test_parse_gate_message_ezk_are_case_insensitive():
    assert parse_gate_message("E 100") == ("e", 100)
    assert parse_gate_message("z 100") == ("z", 100)
    assert parse_gate_message("K 100") == ("k", 100)


def test_parse_gate_message_still_rejects_truly_unknown_letters():
    for letter in ("f", "g", "y", "m"):
        assert parse_gate_message(f"{letter} 100") is None


# ── GATE_NAMES gains e/z/k ──────────────────────────────────────────────────

def test_gate_names_include_ezk():
    from n3x_bot.gates import GATE_NAMES
    assert GATE_NAMES["e"] == "Epsilon Gate"
    assert GATE_NAMES["z"] == "Zeta Gate"
    assert GATE_NAMES["k"] == "Kappa Gate"


# ── build_gate_embed: Epsilon / Zeta / Kappa fields (no reward line) ─────────

def _field_by_name(embed, needle):
    for f in embed.fields:
        if needle in f.name:
            return f
    return None


def test_build_gate_embed_renders_epsilon_field_with_lf4_rate_no_reward():
    totals = {"a": {"count": 1, "avg": 100}}
    rewards = {"a": 46892}
    epsilon = {"count": 4, "avg": 46892, "rates": {"lf4": 25.0}}

    embed = build_gate_embed(totals, rewards, "05.07.2026 12:00", epsilon=epsilon)

    field = _field_by_name(embed, "Epsilon Gate")
    assert field is not None
    assert "🔷 Epsilon Gate" == field.name
    assert "LF4 Drop Rate" in field.value
    assert "25.0" in field.value
    assert "Reward" not in field.value  # e/z/k carry no reward


def test_build_gate_embed_renders_zeta_field_with_havoc_rate_no_reward():
    totals = {}
    rewards = {}
    zeta = {"count": 2, "avg": 1234, "rates": {"havoc": 50.0}}

    embed = build_gate_embed(totals, rewards, "05.07.2026 12:00", zeta=zeta)

    field = _field_by_name(embed, "Zeta Gate")
    assert field is not None
    assert "🔷 Zeta Gate" == field.name
    assert "Havoc Drop Rate" in field.value
    assert "Reward" not in field.value


def test_build_gate_embed_renders_kappa_field_with_two_rates_no_reward():
    totals = {}
    rewards = {}
    kappa = {"count": 3, "avg": 500,
             "rates": {"hercules": 66.7, "lf4u": 33.3}}

    embed = build_gate_embed(totals, rewards, "05.07.2026 12:00", kappa=kappa)

    field = _field_by_name(embed, "Kappa Gate")
    assert field is not None
    assert "🔷 Kappa Gate" == field.name
    assert "Hercules Drop Rate" in field.value
    assert "LF4-U Drop Rate" in field.value
    assert "Reward" not in field.value


def test_build_gate_embed_delta_field_still_carries_reward():
    # Regression guard for the generalization: Delta keeps its Reward line even
    # once e/z/k (rewardless) fields exist.
    totals = {}
    rewards = {"d": 75361}
    delta = {"count": 2, "avg": 75000, "laser_rate": 50.0}

    embed = build_gate_embed(totals, rewards, "05.07.2026 12:00", delta=delta)

    field = _field_by_name(embed, "Delta Gate")
    assert field is not None
    assert "Reward" in field.value
