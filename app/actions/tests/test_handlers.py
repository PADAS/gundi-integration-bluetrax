import datetime
import json

import httpx
import pytest

from gundi_core.schemas.v2 import Integration

from app.actions import handlers
from app.actions.configurations import AuthenticateConfig, PullEventsConfig
from app.bluetrax_v202503 import FleetCurrentLocations, LoginResponse
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
