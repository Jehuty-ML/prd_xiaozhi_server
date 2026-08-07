"""Load plugins_func.functions modules."""

from __future__ import annotations

import importlib
import pkgutil

from loguru import logger


def auto_import_modules(package_name: str = "app.plugins.functions") -> None:
    try:
        package = importlib.import_module(package_name)
    except ImportError as exc:
        logger.error(f"Cannot import plugin package {package_name}: {exc}")
        return
    if not hasattr(package, "__path__"):
        return
    for mod in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
        try:
            importlib.import_module(mod.name)
            logger.debug(f"Loaded plugin module {mod.name}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed loading plugin {mod.name}: {exc}")
