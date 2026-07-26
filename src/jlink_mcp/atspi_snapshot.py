"""Emit a bounded JSON snapshot of the isolated desktop accessibility tree."""

from __future__ import annotations

import contextlib
import json

import pyatspi


def node(accessible: object, depth: int = 0) -> dict[str, object]:
    result: dict[str, object] = {
        "name": getattr(accessible, "name", "") or "",
        "role": getattr(accessible, "getRoleName", lambda: "unknown")(),
        "description": getattr(accessible, "description", "") or "",
        "states": [],
        "children": [],
    }
    with contextlib.suppress(Exception):
        state_set = accessible.getState()
        result["states"] = [
            pyatspi.stateToString(state) for state in state_set.getStates()
        ]
    if depth < 8:
        children = []
        with contextlib.suppress(Exception):
            count = min(int(accessible.childCount), 256)
            for index in range(count):
                children.append(node(accessible.getChildAtIndex(index), depth + 1))
        result["children"] = children
    return result


def main() -> None:
    desktop = pyatspi.Registry.getDesktop(0)
    print(json.dumps(node(desktop), sort_keys=True))


if __name__ == "__main__":
    main()
