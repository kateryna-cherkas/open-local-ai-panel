"""JSONL and CSV helpers."""
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Set


def read_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    if not path.exists():
        return iter(())
    return _iter_jsonl(path)


def _iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def completed_keys(path: Path, key_field: str = "key", skip_errors: bool = True) -> Set[str]:
    """Keys already present in a JSONL file. Records with a non-null `error`
    are not counted as complete when skip_errors is True, so they are retried."""
    keys = set()  # type: Set[str]
    for rec in read_jsonl(path):
        if skip_errors and rec.get("error"):
            continue
        k = rec.get(key_field)
        if k:
            keys.add(k)
    return keys


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
