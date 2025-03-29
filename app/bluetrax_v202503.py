import ssl

from typing import List, Union
from datetime import datetime, timezone, timedelta
import httpx
import pydantic

BLUETRAX_API_URL = "https://public_api.bluetrax.co.ke/api"


class LoginResponse(pydantic.BaseModel):
    userName: str
    token: str
    # From experimentation, the token expires in 24 hours
    tokenxpiry: datetime

class CurrentLocation(pydantic.BaseModel):
    device_timezone: int
    unit_id: str
    fixtime: datetime
    location: str
    speed: Union[int,float]
    course: Union[int,float]
    longitude: float
    latitude: float
    mileage: Union[int,float]
    reg_no:str

    @pydantic.validator("fixtime")
    def _fixtime(cls, val:datetime) -> datetime:
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val

class FleetCurrentLocations(pydantic.BaseModel):
    response: str
    data: List[CurrentLocation] = pydantic.Field(default_factory=list)


class HistoryItem(pydantic.BaseModel):
    unit_id: str
    fixtime: datetime
    #alerts: List[str] # I don't know what type this will be, so ignoring it for now.
    speed: Union[int,float]
    course: Union[int,float]
    longitude: float
    latitude: float
    reg_no: str
    driver: str
    location: str   
    device_timezone: int

    @pydantic.validator("fixtime", pre=True)
    def _fixtime(cls, v):
        return datetime.strptime(v, "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=timezone.utc)


class VehicleHistoryResponse(pydantic.BaseModel):
    response:str
    data: list[HistoryItem] = pydantic.Field(default_factory=list)


async def authenticate(*, username: str, apikey: str) -> LoginResponse:
    
    url = f'{BLUETRAX_API_URL}/Login/Login'
    headers = {
        "Content-Type": "application/json",
    }


    # Create a default SSL context.
    ssl_context = ssl.create_default_context()

    # Enforce TLSv1.2 exclusively by setting both the minimum and maximum version.
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            url,
            json={
                "user_name": username,
                "key": apikey
            }, 
            headers=headers,
        )
        response.raise_for_status()
        return LoginResponse.parse_obj(response.json())


async def get_fleet_current_locations(token:str) -> FleetCurrentLocations:
    url = f'{BLUETRAX_API_URL}/Public/fleet_current_locations'
    
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}"
            }
        )
        response.raise_for_status()
        return FleetCurrentLocations.parse_obj(response.json())


async def get_vehicle_history(token:str, reg_no:str, start_date:datetime, end_date:datetime) -> VehicleHistoryResponse:
    url = f'{BLUETRAX_API_URL}/Public/get_vehicle_history'
    
    start_at = start_date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_at = end_date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}"
            },
            json={
                "reg_no": reg_no,
                "start_at": start_at,
                "end_at": end_at
            }
        )
        response.raise_for_status()
        data = response.json()
        return VehicleHistoryResponse.parse_obj(data)
