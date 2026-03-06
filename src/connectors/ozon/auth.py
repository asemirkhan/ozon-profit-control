from __future__ import annotations

import os


class MissingOzonCredentialsError(RuntimeError):
    pass


def get_ozon_headers() -> dict[str, str]:
    client_id = os.getenv("OZON_CLIENT_ID")
    api_key = os.getenv("OZON_API_KEY")

    missing = [
        name
        for name, value in {
            "OZON_CLIENT_ID": client_id,
            "OZON_API_KEY": api_key,
        }.items()
        if not value
    ]
    if missing:
        raise MissingOzonCredentialsError("Missing OZON_CLIENT_ID/OZON_API_KEY")

    return {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
