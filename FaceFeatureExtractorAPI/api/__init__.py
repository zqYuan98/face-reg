"""Thin HTTP adapters for the independently usable algorithm package."""

from .app import create_app, create_default_app

__all__ = ["create_app", "create_default_app"]
