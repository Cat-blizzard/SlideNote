from __future__ import annotations

import argparse

from slidenote.build.config import (
    _apply_build_preset_defaults,
    _apply_note_profile_defaults,
    _apply_speed_mode_defaults,
    _friendly_build_error,
)
from slidenote.build.errors import UserFacingConfigError
from slidenote.build.stages import BUILD_STAGES, _print_build_outputs
from slidenote.build.state import create_build_state
from slidenote.exporting import parse_export_formats


def run_build(args: argparse.Namespace) -> int:
    _apply_build_preset_defaults(args)
    _apply_speed_mode_defaults(args)
    _apply_note_profile_defaults(args)
    try:
        export_formats = parse_export_formats(args.export)
    except ValueError as exc:
        raise UserFacingConfigError(str(exc)) from exc

    state = create_build_state(args, export_formats)
    try:
        for stage in BUILD_STAGES:
            stage(state)
    except Exception as exc:
        friendly_message = _friendly_build_error(exc, args)
        if friendly_message:
            state.progress.fail(friendly_message)
            raise UserFacingConfigError(friendly_message) from exc
        state.progress.fail(str(exc))
        raise

    _print_build_outputs(state)
    return state.export_exit_code
