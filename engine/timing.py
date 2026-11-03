"""Offset-aware conversions between grid bars and audio seconds."""
import math


def _finite(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number.") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number.")
    return number


def grid_origin(grid):
    t1 = _finite(grid.get("t1"), "Grid t1")
    bar_s = _finite(grid.get("bar_s"), "Grid bar_s")
    if bar_s <= 0:
        raise ValueError("Grid bar_s must be positive.")
    return t1 - _finite(grid.get("offset_ms", 0), "Grid offset_ms") / 1000


def bar_to_time(bar, grid):
    return _finite(grid_origin(grid) + _finite(bar, "Bar position") * float(grid["bar_s"]), "Audio time")


def time_to_bar(seconds, grid):
    return _finite((_finite(seconds, "Audio time") - grid_origin(grid)) / float(grid["bar_s"]), "Bar position")
