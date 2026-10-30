from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class MissingWbCredentialsError(RuntimeError):
    pass


def get_wb_headers() -> dict[str, str]:
    api_token = os.getenv("WB_API_TOKEN")
    api_key = os.getenv("WB_API_KEY")
    auth_value = api_token or api_key
    if not auth_value:
        raise MissingWbCredentialsError("Missing WB_API_TOKEN/WB_API_KEY")

    if api_token and api_key:
        logger.info("WB auth: using WB_API_TOKEN (WB_API_KEY is ignored due to priority)")

    headers = {
        "Authorization": auth_value,
        "Content-Type": "application/json",
    }
    client_secret = os.getenv("WB_CLIENT_SECRET")
    if client_secret:
        headers["X-Client-Secret"] = client_secret
    return headers
