"""Thin HTTP adapters; importing a lightweight route does not load model code."""

from typing import Any

__all__ = ["create_app", "create_default_app"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    from .app import create_app, create_default_app

    factories = {"create_app": create_app, "create_default_app": create_default_app}
    globals().update(factories)
    return factories[name]
