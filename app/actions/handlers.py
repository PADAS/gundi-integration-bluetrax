import httpx
import logging
from datetime import datetime, timezone, timedelta
from app.actions.configurations import AuthenticateConfig, PullEventsConfig
from app.services.activity_logger import activity_logger
from app.services.action_scheduler import crontab_schedule
from app.services.gundi import send_observations_to_gundi
from app.services.state import IntegrationStateManager
from app.services.errors import ConfigurationNotFound, ConfigurationValidationError
from app.services.utils import find_config_for_action
from gundi_core.schemas.v2 import Integration
from pydantic import BaseModel, parse_obj_as
from typing import List, Optional, Iterable, Generator, Any

from app.bluetrax_v202503 import authenticate, get_fleet_current_locations, \
    get_vehicle_history, CurrentLocation, HistoryItem, LoginResponse


logger = logging.getLogger(__name__)
state_manager = IntegrationStateManager()


def get_auth_config(integration):
    # Look for the login credentials, needed for any action
    auth_config = find_config_for_action(
        configurations=integration.configurations,
        action_id="auth"
    )
    if not auth_config:
        raise ConfigurationNotFound(
            f"Authentication settings for integration {str(integration.id)} "
            f"are missing. Please fix the integration setup in the portal."
        )
    return AuthenticateConfig.parse_obj(auth_config.data)


async def action_auth(integration:Integration, action_config: AuthenticateConfig):
    logger.info(f"Executing auth action with integration {integration} and action_config {action_config}...")

    try:
        # Ignore cached credentials, because this action is meant for validating configuration.
        auth = await authenticate(username=action_config.username, apikey=action_config.apikey.get_secret_value())
        ex = auth.tokenxpiry - datetime.now(tz=timezone.utc) - timedelta(seconds=15)
        await state_manager.set_state(integration_id=integration.id, action_id='auth', state=auth.dict(), ex=ex)

        return {"valid_credentials": True}

    except httpx.HTTPStatusError as e:
        return {"valid_credentials": False, "status_code": e.response.status_code}


@crontab_schedule("*/10 * * * *")
@activity_logger()
async def action_pull_observations(integration:Integration, action_config: PullEventsConfig) -> dict:
    logger.info(f"Executing pull_observations action with integration {integration} and action_config {action_config}...")

    if saved_loginresponse := await state_manager.get_state(
        integration_id=integration.id,
        action_id='auth'
    ):
        auth = LoginResponse.parse_obj(saved_loginresponse)
    else:
        auth_config = get_auth_config(integration)
        auth = await authenticate(username=auth_config.username, apikey=auth_config.apikey.get_secret_value())
        ex = auth.tokenxpiry - datetime.now(tz=timezone.utc) - timedelta(seconds=15)
        await state_manager.set_state(integration_id=integration.id, action_id='auth', state=auth.dict(), ex=ex)

    currentLocations = await get_fleet_current_locations(token=auth.token)

    observations = [transform_current_location(item) for item in currentLocations.data]

    await send_observations_to_gundi(list(observations), integration_id=integration.id)
    
    accumulator = []
    for item in currentLocations.data:
        historyLocations = await get_vehicle_history(
            token=auth.token,
            reg_no=item.reg_no,
            start_date=datetime.now(tz=timezone.utc) - timedelta(minutes=10),
            end_date=datetime.now(tz=timezone.utc)
        )

        accumulator.extend([transform_history_item(item) for item in historyLocations.data])
    if accumulator:
        await send_observations_to_gundi(accumulator, integration_id=integration.id)

    return {'finished': True}


def transform_current_location(item: CurrentLocation):
    return {
        "source_name": item.reg_no,
        "source": item.unit_id,
        "type": "tracking-device",
        "subject_type": "vehicle",
        "recorded_at": item.fixtime,
        "location": {
            "lat": item.latitude,
            "lon": item.longitude
        },
        "additional": {
            "speed_kmph": item.speed,
            "location": item.location,
            "course": item.course,
            "device_timezone": item.device_timezone,
            "unit_id": item.unit_id,
            "subject_name": item.reg_no,
            "_from": "current_location"
        }
    }


def transform_history_item(item: HistoryItem):
    return {
        "source_name": item.reg_no,
        "source": item.unit_id,
        "type": "tracking-device",
        "subject_type": "vehicle",
        "recorded_at": item.fixtime,
        "location": {
            "lat": item.latitude,
            "lon": item.longitude
        },
        "additional": {
            "speed_kmph": item.speed,
            "location": item.location,
            "driver": item.driver,
            "course": item.course,
            "device_timezone": item.device_timezone,
            "unit_id": item.unit_id,
            "subject_name": item.reg_no,
            "_from": "history_item"
        }
    }


