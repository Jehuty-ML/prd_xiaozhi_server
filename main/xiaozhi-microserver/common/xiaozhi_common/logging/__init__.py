from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logging(log_level: str = "INFO", log_file: str = "logs/server.log") -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    error_file = str(Path(log_file).with_name("error.log"))
    logger.remove()
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | {message}",
    )
    logger.add(log_file, level=log_level, rotation="50 MB", retention="7 days")
    logger.add(error_file, level="ERROR", rotation="20 MB", retention="14 days")
