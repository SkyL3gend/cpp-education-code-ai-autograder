import io
import urllib.error

import pytest

from cpp_assessor.llm.provider import LLMClient, LLMError, LLMSettings


@pytest.mark.parametrize("status,code", [(402, "MODEL_BALANCE_INSUFFICIENT"), (401, "MODEL_ACCESS_DENIED")])
def test_account_errors_stop_after_one_request(monkeypatch, status, code):
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise urllib.error.HTTPError("https://model.invalid", status, "account error", {},
            io.BytesIO(b'{"error":{"message":"Insufficient Balance"}}'))

    monkeypatch.setattr("urllib.request.urlopen", fail)
    client = LLMClient(LLMSettings(api_key="test-only-no-network"))
    with pytest.raises(LLMError) as caught:
        client.complete_json(system="JSON", user="test", call_name="account_probe")
    assert caught.value.code == code
    assert not caught.value.retryable
    assert len(attempts) == 1
    assert "test-only-no-network" not in str(caught.value)
