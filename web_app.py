"""Development and production entry point for FastShop."""

from fasthtml.common import serve

from app.config import settings
from app.main import app  # noqa: F401 - discovered by the ASGI server

if __name__ == "__main__":
    serve(appname="web_app", app="app", host=settings.host, port=settings.port, reload=False)
