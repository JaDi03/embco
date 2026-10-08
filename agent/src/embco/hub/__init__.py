"""The hub: the API the dashboard uses to connect a shop's ERP and start its own agent."""

from embco.hub.api import create_app
from embco.hub.server import build_app, serve

__all__ = ["build_app", "create_app", "serve"]
