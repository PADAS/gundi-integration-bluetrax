import httpx
import pytest

from app.bluetrax_v202503 import BluetraxLoginRejected, authenticate


def _post_returning(response_kwargs):
    async def post(self, url, **kwargs):
        return httpx.Response(request=httpx.Request("POST", url), **response_kwargs)

    return post


@pytest.mark.asyncio
async def test_authenticate_raises_login_rejected_with_the_reason_bluetrax_gave(mocker):
    mocker.patch(
        "httpx.AsyncClient.post",
        new=_post_returning(
            {"status_code": 400, "json": {"Error": ["Invalid Credentials, please try again"]}}
        ),
    )

    with pytest.raises(BluetraxLoginRejected) as raised:
        await authenticate(username="someone", apikey="a-wrong-key")

    assert raised.value.reason == "Invalid Credentials, please try again"
    assert raised.value.response.status_code == 400
    assert "a-wrong-key" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_authenticate_raises_login_rejected_without_a_readable_body(mocker, status_code):
    mocker.patch(
        "httpx.AsyncClient.post",
        new=_post_returning({"status_code": status_code, "text": "<html>denied</html>"}),
    )

    with pytest.raises(BluetraxLoginRejected) as raised:
        await authenticate(username="someone", apikey="a-wrong-key")

    assert raised.value.reason == ""


@pytest.mark.asyncio
async def test_authenticate_leaves_other_failures_as_plain_http_errors(mocker):
    mocker.patch(
        "httpx.AsyncClient.post",
        new=_post_returning({"status_code": 503, "text": "unavailable"}),
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        await authenticate(username="someone", apikey="a-key")

    assert not isinstance(raised.value, BluetraxLoginRejected)
