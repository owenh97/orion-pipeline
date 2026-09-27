"""Object-storage abstraction.

The brief says documents are referenced from object storage. Production would
be S3/GCS; locally we back it with the filesystem. Both sit behind one
interface so the pipeline code never knows which it is talking to -- the only
thing needed to run this on S3 is a second ~20-line adapter.

URI form: local://<submission_id>/documents/<filename>
          s3://<bucket>/<key>
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path


class ObjectStore(ABC):
    @abstractmethod
    def read_bytes(self, uri: str) -> bytes: ...

    @abstractmethod
    def exists(self, uri: str) -> bool: ...

    def sha256(self, uri: str) -> str:
        return hashlib.sha256(self.read_bytes(uri)).hexdigest()


class LocalObjectStore(ObjectStore):
    """Filesystem-backed store rooted at a single directory.

    Paths are resolved and checked against the root so a malicious submission
    cannot reference ../../etc/passwd.
    """

    SCHEME = "local://"

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def _resolve(self, uri: str) -> Path:
        rel = uri[len(self.SCHEME):] if uri.startswith(self.SCHEME) else uri
        candidate = (self.root / rel).resolve()
        if not str(candidate).startswith(str(self.root)):
            raise ValueError(f"URI escapes storage root: {uri}")
        return candidate

    def read_bytes(self, uri: str) -> bytes:
        path = self._resolve(uri)
        if not path.is_file():
            raise FileNotFoundError(f"object not found: {uri} -> {path}")
        return path.read_bytes()

    def exists(self, uri: str) -> bool:
        try:
            return self._resolve(uri).is_file()
        except ValueError:
            return False


class S3ObjectStore(ObjectStore):  # pragma: no cover - not exercised locally
    """Placeholder showing the swap is a drop-in.

    Deliberately not implemented: adding boto3 to satisfy a constraint we
    cannot test here would be decoration, not engineering.
    """

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    def read_bytes(self, uri: str) -> bytes:
        raise NotImplementedError("wire up boto3 get_object here")

    def exists(self, uri: str) -> bool:
        raise NotImplementedError("wire up boto3 head_object here")


def build_store(root: Path, scheme: str = "local") -> ObjectStore:
    if scheme == "local":
        return LocalObjectStore(root)
    if scheme == "s3":
        return S3ObjectStore(bucket=str(root))
    raise ValueError(f"unknown storage scheme: {scheme}")
