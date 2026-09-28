"""Helpers shared by the no-API smoke scripts in this directory."""

from __future__ import annotations

import os
import subprocess

from slidenote.llm import PROVIDERS

OCR_ENV_KEYS = frozenset(
    {
        "BAIDU_OCR_API_KEY",
        "BAIDU_OCR_SECRET_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_VISION_API_KEY",
        "MATHPIX_APP_ID",
        "MATHPIX_APP_KEY",
    }
)
# Every credential a SlideNote run could pick up; derived from the provider registry.
API_ENV_KEYS = frozenset(
    {"SLIDENOTE_API_KEY", *OCR_ENV_KEYS, *(key for spec in PROVIDERS.values() for key in spec.api_key_envs)}
)


def env_without(keys: frozenset[str]) -> dict[str, str]:
    env = dict(os.environ)
    for key in keys:
        env.pop(key, None)
    return env


def run(command: list[str], *, env: dict[str, str]) -> None:
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {' '.join(command)}\n{completed.stdout}")
