from __future__ import annotations

import logging
import os

from .utils import ensure_dir


def setup_logging(log_path: str) -> logging.Logger:
    ensure_dir(os.path.dirname(log_path))
    logger = logging.getLogger("a8_automation")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
