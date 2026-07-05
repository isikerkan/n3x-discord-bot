from n3x_bot.models import Stat, Message, render_output


def test_default_output_when_no_message():
    stat = Stat(id=1, key="tit", name="Tit", message_id=None)
    assert render_output(stat, None, "Erkan", 5) == "Tit — Erkan — 5"


def test_linked_message_renders_placeholders():
    stat = Stat(id=1, key="tit", name="Tit", message_id=9)
    msg = Message(id=9, name="tit_msg", template="{user} did {stat} x{count}")
    assert render_output(stat, msg, "Erkan", 5) == "Erkan did Tit x5"


def test_missing_placeholder_in_template_is_ignored():
    stat = Stat(id=1, key="cry", name="Cry", message_id=9)
    msg = Message(id=9, name="cry_msg", template="cried {count} times")
    assert render_output(stat, msg, "Ali", 3) == "cried 3 times"
