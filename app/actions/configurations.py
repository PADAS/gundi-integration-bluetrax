import pydantic
from app.services.utils import FieldWithUIOptions, GlobalUISchemaOptions
from .core import AuthActionConfiguration, PullActionConfiguration, ExecutableActionMixin


class AuthenticateConfig(AuthActionConfiguration, ExecutableActionMixin):
    username: str = pydantic.Field(..., title = "Username", description = "Username for Bluetrax account")
    apikey: pydantic.SecretStr = pydantic.Field(..., title = "Bluetrax API Key", 
                                description = "A special API key that is required for accessing the Bluetrax API.",
                                format="password")

    ui_global_options: GlobalUISchemaOptions = GlobalUISchemaOptions(
        order=[
            "username",
            "apikey",
        ],
    )


class PullEventsConfig(PullActionConfiguration):
    pass