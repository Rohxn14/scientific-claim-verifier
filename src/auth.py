import os
import secrets
from fastapi import Header, HTTPException
from dotenv import load_dotenv

load_dotenv()


def auth_required() -> bool:
    return bool(os.environ.get("API_KEY"))


def require_api_key(x_api_key: str = Header(None)):
    """Guards endpoints that change collections or spend LLM quota on admin
    tasks. With no API_KEY configured, auth is disabled (local dev)."""
    expected = os.environ.get("API_KEY")
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
