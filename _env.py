#!/usr/bin/env python3
"""
Auto-load environment variables for all ai-digest scripts.

Кладётся в корень проекта и импортируется первым делом в каждом скрипте.
Ищет .env в двух местах:
  1. ~/.hermes/.env   — основной (тут все ключи)
  2. ./.env           — локальный (для portability)

Использование:
    from _env import _; _()      # загрузить переменные один раз
    # дальше os.environ.get("KEY", "")
"""
import os
from pathlib import Path

from dotenv import load_dotenv


def _load(path: Path) -> bool:
    """Загрузить .env из указанного пути. Return True если файл существует."""
    if path.exists():
        load_dotenv(path, override=False)
        return True
    return False


def __get_env():
    hermes_env = Path.home() / ".hermes" / ".env"
    local_env  = Path(__file__).parent / ".env"

    loaded = []
    if _load(hermes_env):
        loaded.append(str(hermes_env))
    if _load(local_env):
        loaded.append(str(local_env))

    if loaded:
        print(f"[_env] Loaded: {', '.join(loaded)}", flush=True)
    else:
        print("[_env] WARNING: no .env file found — API keys may be missing", flush=True)


# Автозагрузка при импорте
__get_env()

# shorthand для вызова из других модулей
_ = __get_env
