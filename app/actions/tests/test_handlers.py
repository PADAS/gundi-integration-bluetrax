import datetime
import json

import httpx
import pytest

from gundi_core.schemas.v2 import Integration

from app.actions import handlers
from app.actions.configurations import AuthenticateConfig, PullEventsConfig
from app.bluetrax_v202503 import BluetraxLoginRejected, FleetCurrentLocations, LoginResponse
from app.services.errors import IntegrationAuthError, format_error_message
from app.services.retry_policies import is_retryable_failure
from app.services.state import IntegrationStateManager


@pytest.fixture
def bluetrax_integration(integration_v2_as_dict):
    """The shared fixture with its auth config swapped for Bluetrax credentials."""
    integration = dict(integration_v2_as_dict)
    integration["configurations"] = [
        config
        for config in integration["configurations"]
        if config["action"]["value"] != "auth"
    ] + [
        {
            "id": "30f8878c-4a98-4c95-88eb-79f73c40fb2f",
            "integration": integration["id"],
            "action": {
                "id": "80448d1c-4696-4b32-a59f-f3494fc949ac",
                "type": "auth",
                "name": "Authenticate",
                "value": "auth",
            },
            "data": {"username": "bluetrax-user", "apikey": "bluetrax-api-key"},
        },
    ]
    return Integration.parse_obj(integration)


@pytest.fixture
def auth_config():
    return AuthenticateConfig(username="bluetrax-user", apikey="bluetrax-api-key")


@pytest.fixture
def login_response():
    return LoginResponse(
        userName="bluetrax-user",
        token="a-bluetrax-token",
        tokenxpiry=datetime.datetime.now(tz=datetime.timezone.utc)
        + datetime.timedelta(hours=24),
    )


@pytest.fixture
def state_manager_on_mock_redis(mocker, mock_redis_empty):
    """The real state manager, writing to a mock Redis.

    The handlers module builds its own IntegrationStateManager at import time,
    so patching app.services.state.redis alone would not reach it.
    """
    mocker.patch("app.services.state.redis", mock_redis_empty)
    mocker.patch.object(handlers, "state_manager", IntegrationStateManager())
    return mock_redis_empty.Redis.return_value


@pytest.mark.asyncio
async def test_action_auth_caches_the_login_response_until_the_token_expires(
    mocker, state_manager_on_mock_redis, bluetrax_integration, auth_config, login_response
):
    mocker.patch.object(
        handlers, "authenticate", mocker.AsyncMock(return_value=login_response)
    )

    result = await handlers.action_auth(bluetrax_integration, auth_config)

    assert result == {"valid_credentials": True}
    key, value = state_manager_on_mock_redis.set.call_args.args
    assert key == f"integration_state.{bluetrax_integration.id}.auth.no-source"
    assert json.loads(value)["token"] == "a-bluetrax-token"
    # The cache must expire with the token, not outlive it.
    ttl = state_manager_on_mock_redis.set.call_args.kwargs["ex"]
    assert 0 < ttl <= 24 * 60 * 60


def _http_status_error(status_code):
    request = httpx.Request("POST", "https://public-api.bluetrax.co.ke/api/Login/Login")
    return httpx.HTTPStatusError(
        f"{status_code}", request=request, response=httpx.Response(status_code, request=request)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403])
async def test_action_auth_reports_invalid_credentials_when_bluetrax_rejects_the_login(
    mocker, state_manager_on_mock_redis, bluetrax_integration, auth_config, status_code
):
    mocker.patch.object(
        handlers, "authenticate", mocker.AsyncMock(side_effect=_http_status_error(status_code))
    )

    result = await handlers.action_auth(bluetrax_integration, auth_config)

    assert result == {"valid_credentials": False, "status_code": status_code}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        _http_status_error(404),
        _http_status_error(503),
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
    ],
)
async def test_action_auth_raises_when_bluetrax_does_not_answer_the_login(
    mocker, state_manager_on_mock_redis, bluetrax_integration, auth_config, error
):
    mocker.patch.object(handlers, "authenticate", mocker.AsyncMock(side_effect=error))

    with pytest.raises(type(error)):
        await handlers.action_auth(bluetrax_integration, auth_config)


@pytest.mark.asyncio
async def test_pull_observations_authenticates_and_caches_when_nothing_is_cached(
    mocker,
    state_manager_on_mock_redis,
    bluetrax_integration,
    login_response,
    mock_publish_event,
):
    mocker.patch("app.services.activity_logger.publish_event", mock_publish_event)
    mocker.patch.object(
        handlers, "authenticate", mocker.AsyncMock(return_value=login_response)
    )
    mocker.patch.object(
        handlers,
        "get_fleet_current_locations",
        mocker.AsyncMock(return_value=FleetCurrentLocations(response="success", data=[])),
    )
    send_observations = mocker.patch.object(
        handlers, "send_observations_to_gundi", mocker.AsyncMock(return_value=[])
    )

    result = await handlers.action_pull_observations(
        integration=bluetrax_integration, action_config=PullEventsConfig()
    )

    assert result == {"finished": True}
    send_observations.assert_awaited_once()
    key, value = state_manager_on_mock_redis.set.call_args.args
    assert key == f"integration_state.{bluetrax_integration.id}.auth.no-source"
    assert json.loads(value)["token"] == "a-bluetrax-token"
    assert 0 < state_manager_on_mock_redis.set.call_args.kwargs["ex"] <= 24 * 60 * 60


@pytest.mark.asyncio
async def test_pull_observations_pauses_for_an_hour_on_403(
    mocker,
    state_manager_on_mock_redis,
    bluetrax_integration,
    login_response,
    mock_publish_event,
):
    mocker.patch("app.services.activity_logger.publish_event", mock_publish_event)
    mocker.patch.object(
        handlers, "authenticate", mocker.AsyncMock(return_value=login_response)
    )
    forbidden = httpx.HTTPStatusError(
        "Forbidden",
        request=httpx.Request("GET", "https://example.org/fleet"),
        response=httpx.Response(403, text="quota exceeded"),
    )
    mocker.patch.object(
        handlers, "get_fleet_current_locations", mocker.AsyncMock(side_effect=forbidden)
    )

    with pytest.raises(httpx.HTTPStatusError):
        await handlers.action_pull_observations(
            integration=bluetrax_integration, action_config=PullEventsConfig()
        )

    quiet_writes = [
        call
        for call in state_manager_on_mock_redis.set.call_args_list
        if call.args[0].endswith(".pull_observations_quiet.no-source")
    ]
    assert len(quiet_writes) == 1
    assert json.loads(quiet_writes[0].args[1])["paused"] is True
    assert quiet_writes[0].kwargs["ex"] == 60 * 60


def _login_rejected(status_code=400, reason="Invalid Credentials, please try again"):
    request = httpx.Request("POST", "https://public-api.bluetrax.co.ke/api/Login/Login")
    return BluetraxLoginRejected(
        f"Bluetrax rejected the login with status {status_code}",
        request=request,
        response=httpx.Response(status_code, request=request, json={"Error": [reason]}),
        reason=reason,
    )


def _quiet_writes(redis_client):
    return [
        call
        for call in redis_client.set.call_args_list
        if call.args[0].endswith(".pull_observations_quiet.no-source")
    ]


@pytest.mark.asyncio
async def test_pull_observations_reports_a_rejected_login_as_an_auth_error_and_pauses(
    mocker, state_manager_on_mock_redis, bluetrax_integration, mock_publish_event
):
    mocker.patch("app.services.activity_logger.publish_event", mock_publish_event)
    mocker.patch.object(
        handlers, "authenticate", mocker.AsyncMock(side_effect=_login_rejected())
    )

    with pytest.raises(IntegrationAuthError) as raised:
        await handlers.action_pull_observations(
            integration=bluetrax_integration, action_config=PullEventsConfig()
        )

    assert format_error_message(raised.value) == (
        "Authentication failed — Bluetrax rejected the login: Invalid Credentials, "
        "please try again. Check the username and API key. (HTTP 400)"
    )
    assert is_retryable_failure(raised.value) is False
    quiet_writes = _quiet_writes(state_manager_on_mock_redis)
    assert len(quiet_writes) == 1
    assert quiet_writes[0].kwargs["ex"] == handlers.LOGIN_REJECTED_PAUSE_SECONDS
    stored = quiet_writes[0].args[1]
    assert json.loads(stored)["paused"] is True
    # Neither credential is written to the state store.
    assert "bluetrax-api-key" not in stored
    assert "bluetrax-user" not in stored


def _paused_for(mocker, redis_client, auth_config):
    state = {
        "paused": True,
        "reason": "Bluetrax rejected the saved credentials.",
        "credentials": handlers.credentials_fingerprint(auth_config),
    }

    async def get(key):
        return json.dumps(state) if key.endswith(".pull_observations_quiet.no-source") else None

    redis_client.get = mocker.AsyncMock(side_effect=get)


@pytest.mark.asyncio
async def test_pull_observations_stays_quiet_while_the_rejected_credentials_are_unchanged(
    mocker, state_manager_on_mock_redis, bluetrax_integration, auth_config, mock_publish_event
):
    mocker.patch("app.services.activity_logger.publish_event", mock_publish_event)
    _paused_for(mocker, state_manager_on_mock_redis, auth_config)
    authenticate = mocker.patch.object(handlers, "authenticate", mocker.AsyncMock())

    result = await handlers.action_pull_observations(
        integration=bluetrax_integration, action_config=PullEventsConfig()
    )

    assert result == {"paused": True, "reason": "Bluetrax rejected the saved credentials."}
    authenticate.assert_not_awaited()


@pytest.mark.asyncio
async def test_pull_observations_resumes_as_soon_as_the_credentials_change(
    mocker, state_manager_on_mock_redis, bluetrax_integration, login_response, mock_publish_event
):
    mocker.patch("app.services.activity_logger.publish_event", mock_publish_event)
    _paused_for(
        mocker, state_manager_on_mock_redis,
        AuthenticateConfig(username="bluetrax-user", apikey="the-old-rejected-key"),
    )
    authenticate = mocker.patch.object(
        handlers, "authenticate", mocker.AsyncMock(return_value=login_response)
    )
    mocker.patch.object(
        handlers,
        "get_fleet_current_locations",
        mocker.AsyncMock(return_value=FleetCurrentLocations(response="success", data=[])),
    )
    mocker.patch.object(handlers, "send_observations_to_gundi", mocker.AsyncMock(return_value=[]))

    result = await handlers.action_pull_observations(
        integration=bluetrax_integration, action_config=PullEventsConfig()
    )

    assert result == {"finished": True}
    authenticate.assert_awaited_once()
    state_manager_on_mock_redis.delete.assert_called_once_with(
        f"integration_state.{bluetrax_integration.id}.pull_observations_quiet.no-source"
    )
