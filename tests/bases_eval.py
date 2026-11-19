"""Small, non-executing evaluator for the expressions used by the shipped Bases."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from knowledgeos.notes import load_yaml


def matches(base_path: Path, view_name: str, note, *, mtime: datetime, now: datetime) -> bool:
    base = load_yaml(base_path.read_text(encoding="utf-8"))
    view = next(view for view in base["views"] if view["name"] == view_name)
    return evaluate(base.get("filters"), note, mtime=mtime, now=now) and evaluate(view.get("filters"), note, mtime=mtime, now=now)


def evaluate(expression, note, *, mtime: datetime, now: datetime) -> bool:
    if expression is None:
        return True
    if isinstance(expression, list):
        return all(evaluate(item, note, mtime=mtime, now=now) for item in expression)
    if isinstance(expression, dict):
        if set(expression) == {"and"}:
            return all(evaluate(item, note, mtime=mtime, now=now) for item in expression["and"])
        if set(expression) == {"or"}:
            return any(evaluate(item, note, mtime=mtime, now=now) for item in expression["or"])
        raise ValueError(f"unsupported Bases filter: {expression}")

    def operand(text):
        text = text.strip()
        if text == "now()":
            return now
        duration = re.fullmatch(r'now\(\)\s*-\s*"(\d+) days?"', text)
        if duration:
            return now - timedelta(days=int(duration.group(1)))
        if text == "file.ext":
            return note.path.rsplit(".", 1)[-1]
        if text == "file.mtime":
            return mtime
        folder = re.fullmatch(r'file\.inFolder\((".*")\)', text)
        if folder:
            return note.path.startswith(json.loads(folder.group(1)).rstrip("/") + "/")
        if text.startswith('"'):
            return json.loads(text)
        if text in ("true", "false", "null"):
            return json.loads(text)
        prop = text.removeprefix("note.")
        if not re.fullmatch(r"[\w-]+", prop):
            raise ValueError(f"unsupported Bases operand: {text}")
        value = note.data.get(prop)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
        return value

    comparison = re.fullmatch(r"(.+?)\s*(==|!=|<|>)\s*(.+)", expression)
    if not comparison:
        return bool(operand(expression))
    left, op, right = comparison.groups()
    left, right = operand(left), operand(right)
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if left is None or right is None:
        return False
    return left < right if op == "<" else left > right
