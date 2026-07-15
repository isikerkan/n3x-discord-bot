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

def test_build_gate_embed_sets_german_title_and_footer():
    totals = {"a": {"count": 2, "avg": 46500}}
    rewards = {"a": 46892, "b": 93820, "c": 139522}

    embed = build_gate_embed(totals, rewards, "05.07.2026 12:00")

    assert embed.title == "📊 Gate Statistik"
    assert embed.footer.text == "Letztes Update: 05.07.2026 12:00"


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
    assert "🟦 Epsilon Gate" == field.name
    assert "LF4: 25.0 %" in field.value
    assert "Belohnung" not in field.value  # e/z/k carry no reward


def test_build_gate_embed_renders_zeta_field_with_havoc_rate_no_reward():
    totals = {}
    rewards = {}
    zeta = {"count": 2, "avg": 1234, "rates": {"havoc": 50.0}}

    embed = build_gate_embed(totals, rewards, "05.07.2026 12:00", zeta=zeta)

    field = _field_by_name(embed, "Zeta Gate")
    assert field is not None
    assert "🟪 Zeta Gate" == field.name
    assert "Havoc: 50.0 %" in field.value
    assert "Belohnung" not in field.value


def test_build_gate_embed_renders_kappa_field_with_two_rates_no_reward():
    totals = {}
    rewards = {}
    kappa = {"count": 3, "avg": 500,
             "rates": {"hercules": 66.7, "lf4u": 33.3}}

    embed = build_gate_embed(totals, rewards, "05.07.2026 12:00", kappa=kappa)

    field = _field_by_name(embed, "Kappa Gate")
    assert field is not None
    assert "🟩 Kappa Gate" == field.name
    assert "Hercules: 66.7 %" in field.value
    assert "LF4-U: 33.3 %" in field.value
    assert "Belohnung" not in field.value


def test_build_gate_embed_delta_field_still_carries_reward():
    # Regression guard for the generalization: Delta keeps its Belohnung line
    # even once e/z/k (rewardless) fields exist.
    totals = {}
    rewards = {"d": 75361}
    delta = {"count": 2, "avg": 75000, "laser_rate": 50.0}

    embed = build_gate_embed(totals, rewards, "05.07.2026 12:00", delta=delta)

    field = _field_by_name(embed, "Delta Gate")
    assert field is not None
    assert "Belohnung" in field.value


# ── build_gate_embed: NEW uniform German inline-field grid ───────────────────
# The reformatted embed drops the description blob entirely: every gate is a
# uniform inline field. A zero-width-space spacer field pads row 2 so that
# Zeta + Kappa begin a fresh row. Field sequence: a, b, c, d, e, SPACER, z, k.

ZWSP = "​"

_EXPECTED_FIELD_NAMES = [
    "🅰 Alpha Gate", "🅱 Beta Gate", "🇨 Gamma Gate", "💎 Delta Gate",
    "🟦 Epsilon Gate", ZWSP, "🟪 Zeta Gate", "🟩 Kappa Gate",
]


def _full_embed(now_str: str = "05.07.2026 12:00"):
    """The production-shaped call: all seven gates present (a/b/c totals rows,
    plus delta + epsilon/zeta/kappa drop dicts). update_gate_stats_embed always
    passes all four extra dicts, so this is the primary rendered case.
    """
    totals = {
        "a": {"count": 2, "avg": 46500},   # reward 46892 > avg -> 🟢
        "b": {"count": 1, "avg": 90000},   # reward 93820 > avg -> 🟢
        "c": {"count": 3, "avg": 140000},  # reward 139522 < avg -> 🔴
    }
    rewards = {"a": 46892, "b": 93820, "c": 139522, "d": 75361}
    delta = {"count": 2, "avg": 75000, "laser_rate": 50.0}
    epsilon = {"count": 4, "avg": 46892, "rates": {"lf4": 25.0}}
    zeta = {"count": 2, "avg": 1234, "rates": {"havoc": 50.0}}
    kappa = {"count": 3, "avg": 500, "rates": {"hercules": 66.7, "lf4u": 33.3}}
    return build_gate_embed(totals, rewards, now_str, delta,
                            epsilon=epsilon, zeta=zeta, kappa=kappa)


def _reward_gate_embed(avg: int, reward: int):
    """A minimal all-seven-present embed exercising a single a-gate row with a
    chosen (avg, reward) so the Gewinn color can be pinned; e/z/k/d zeroed.
    """
    return build_gate_embed(
        {"a": {"count": 1, "avg": avg}},
        {"a": reward},
        "05.07.2026 12:00",
        delta={"count": 0, "avg": 0, "laser_rate": 0.0},
        epsilon={"count": 0, "avg": 0, "rates": {}},
        zeta={"count": 0, "avg": 0, "rates": {}},
        kappa={"count": 0, "avg": 0, "rates": {}},
    )


def test_build_gate_embed_has_no_description_blob():
    assert not _full_embed().description  # None or "" — no a/b/c text blob


def test_build_gate_embed_title_is_gate_statistik():
    assert _full_embed().title == "📊 Gate Statistik"


def test_build_gate_embed_footer_is_letztes_update():
    embed = _full_embed("05.07.2026 12:00")
    assert embed.footer.text == "Letztes Update: 05.07.2026 12:00"


def test_build_gate_embed_field_names_in_exact_order_with_spacer():
    embed = _full_embed()
    assert [f.name for f in embed.fields] == _EXPECTED_FIELD_NAMES


def test_build_gate_embed_spacer_field_sits_between_epsilon_and_zeta():
    embed = _full_embed()
    # index 5 (0-based) is the zero-width spacer that pushes Zeta+Kappa onto a
    # new row; it carries a zero-width name AND value.
    assert embed.fields[5].name == ZWSP
    assert embed.fields[5].value == ZWSP


def test_build_gate_embed_all_fields_are_inline():
    assert all(f.inline is True for f in _full_embed().fields)


def test_build_gate_embed_uses_german_labels():
    blob = "\n".join(f.value for f in _full_embed().fields)
    assert "Läufe" in blob
    assert "Ø Kosten" in blob
    assert "Belohnung" in blob
    assert "Gewinn" in blob


def test_build_gate_embed_drops_all_old_english_strings():
    embed = _full_embed()
    haystack = "".join(
        [embed.title or "", embed.description or "", embed.footer.text or ""]
        + [f.name + f.value for f in embed.fields]
    )
    for banned in ("Total Gates", "Average Cost", "Difference", "Reward",
                   "Last Update", "Gate Statistics", "Runs:", "Drop Rate",
                   "Avg. Cost", "🔷"):
        assert banned not in haystack, f"old string still present: {banned!r}"


def test_build_gate_embed_abc_fields_have_reward_and_profit_no_drops():
    embed = _full_embed()
    for needle in ("Alpha Gate", "Beta Gate", "Gamma Gate"):
        field = _field_by_name(embed, needle)
        assert field is not None
        assert "Läufe" in field.value
        assert "Ø Kosten" in field.value
        assert "Belohnung" in field.value
        assert "Gewinn" in field.value
        assert "%" not in field.value  # a/b/c carry no drop lines


def test_build_gate_embed_ezk_fields_have_drops_and_no_reward():
    embed = _full_embed()
    for needle in ("Epsilon Gate", "Zeta Gate", "Kappa Gate"):
        field = _field_by_name(embed, needle)
        assert field is not None
        assert "Läufe" in field.value
        assert "Ø Kosten" in field.value
        assert "Belohnung" not in field.value
        assert "Gewinn" not in field.value


def test_build_gate_embed_delta_shows_reward_and_laser_drop():
    field = _field_by_name(_full_embed(), "Delta Gate")
    assert field.name == "💎 Delta Gate"
    assert "Belohnung" in field.value
    assert "Laser: 50.0 %" in field.value


def test_build_gate_embed_epsilon_shows_lf4_drop_label():
    field = _field_by_name(_full_embed(), "Epsilon Gate")
    assert "LF4: 25.0 %" in field.value


def test_build_gate_embed_zeta_shows_havoc_drop_label():
    field = _field_by_name(_full_embed(), "Zeta Gate")
    assert "Havoc: 50.0 %" in field.value


def test_build_gate_embed_kappa_shows_both_hercules_and_lf4u_drop_labels():
    field = _field_by_name(_full_embed(), "Kappa Gate")
    assert "Hercules: 66.7 %" in field.value
    assert "LF4-U: 33.3 %" in field.value


def test_build_gate_embed_profit_is_green_when_reward_at_or_above_avg():
    field = _field_by_name(_reward_gate_embed(avg=46000, reward=46892), "Alpha Gate")
    assert field is not None
    assert "🟢" in field.value
    assert "🔴" not in field.value


def test_build_gate_embed_profit_is_red_when_avg_above_reward():
    field = _field_by_name(_reward_gate_embed(avg=50000, reward=46892), "Alpha Gate")
    assert field is not None
    assert "🔴" in field.value
    assert "🟢" not in field.value


def test_build_gate_embed_legacy_all_none_still_renders_abc_fields_no_spacer():
    # Back-compat: the pre-refactor call signature (no delta/e/z/k) must still
    # produce an embed with the a/b/c fields and must NOT crash; the spacer and
    # the d/e/z/k fields are simply absent.
    totals = {"a": {"count": 1, "avg": 46500}}
    rewards = {"a": 46892, "b": 93820, "c": 139522}

    embed = build_gate_embed(totals, rewards, "05.07.2026 12:00")

    names = [f.name for f in embed.fields]
    assert "🅰 Alpha Gate" in names
    assert "🅱 Beta Gate" in names
    assert "🇨 Gamma Gate" in names
    assert "💎 Delta Gate" not in names
    assert ZWSP not in names
