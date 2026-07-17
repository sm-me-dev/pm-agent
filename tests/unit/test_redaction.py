from pm_agent.infrastructure.security.redaction import redact_text, redact_value


def test_redacts_common_secret_shapes():
    text = "Authorization: Bearer abc123 api_key=secret sk-abcdefghijklmnop"
    redacted = redact_text(text)
    assert "abc123" not in redacted
    assert "secret" not in redacted
    assert "sk-abcdefghijklmnop" not in redacted


def test_redacts_nested_values():
    value = {"headers": ["token=my-token"], "safe": 3}
    assert "my-token" not in str(redact_value(value))
