import json
import os
import tempfile
import threading
from pathlib import Path


class StorageError(RuntimeError):
    pass


class JsonStore:
    """Single-process local prototype storage; no production concurrency guarantee."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def _path(self, name: str) -> Path:
        if not name.replace("_", "").isalnum():
            raise StorageError("Invalid collection name")
        return self.root / f"{name}.json"

    def read(self, name: str) -> list[dict]:
        with self.lock:
            path = self._path(name)
            if not path.exists():
                return []
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    raise ValueError("Expected list")
                return data
            except (OSError, ValueError) as exc:
                raise StorageError(f"Cannot read mock collection {name}; inspect local JSON file") from exc

    def write(self, name: str, data: list[dict]) -> None:
        with self.lock:
            path = self._path(name)
            temp = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.root, delete=False, suffix=".tmp") as handle:
                    temp = Path(handle.name)
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, path)
            except OSError as exc:
                raise StorageError(f"Cannot write mock collection {name}") from exc
            finally:
                if temp and temp.exists():
                    temp.unlink()

    def add(self, name: str, item: dict) -> dict:
        with self.lock:
            data = self.read(name)
            data.append(item)
            self.write(name, data)
            return item

    def replace(self, name: str, item: dict) -> dict:
        with self.lock:
            data = self.read(name)
            for index, current in enumerate(data):
                if current["id"] == item["id"]:
                    data[index] = item
                    self.write(name, data)
                    return item
            raise KeyError(item["id"])

    def remove_where(self, name: str, predicate) -> int:
        with self.lock:
            data = self.read(name)
            filtered = [x for x in data if not predicate(x)]
            self.write(name, filtered)
            return len(data) - len(filtered)
