"""File and durable-storage response models.

Split out of :mod:`convilyn.types` for the same reason
:mod:`convilyn._types_builder` was, and by the same instruction: that module sat
exactly at its 800-line budget, and the repo's file-size ratchet says "extract
something instead of raising the number".

This is the seam that costs least now that the Builder block has gone. These
four describe ONE subject — a file the platform holds, and how much durable
storage it occupies — and the only resource that needs all of them is
``client.files``. Nothing else in ``types`` reads them except by name.

**Nothing about the public surface changed.** ``convilyn.types`` re-exports every
name below, so ``from convilyn.types import File``, ``from convilyn import File``
and ``from convilyn.types import *`` all resolve exactly as before. This module
is an implementation detail of that package — import from ``convilyn`` or
``convilyn.types``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class File(BaseModel):
    """A file known to the Convilyn platform.

    Returned by :py:meth:`convilyn.resources.files.AsyncFiles.upload` and,
    in future commits, by ``client.files.get(...)`` / ``client.files.list()``.

    The :py:attr:`file_id` is the only handle other resources need —
    ``client.convert.start(file_id=file.file_id, ...)`` accepts it
    directly. SDK callers should not treat any other field as a stable
    identifier.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    file_id: str = Field(alias="fileId")
    filename: str = Field(alias="fileName")
    size: int = Field(alias="fileSize", gt=0)
    content_type: str = Field(alias="mimeType")
    created_at: datetime = Field(alias="createdAt")
    job_id: str | None = Field(default=None, alias="jobId")
    is_input: bool = Field(default=True, alias="isInput")


class StoredFile(BaseModel):
    """One durable stored file (e.g. an emailed-in attachment).

    Returned inside :class:`FileList` by
    :py:meth:`convilyn.resources.files.AsyncFiles.list`. Durable files
    survive the ~1-hour cleanup that removes ordinary uploads and count
    toward your storage quota. Attribute names mirror :class:`File` for
    consistency; the list wire is snake_case, bridged by ``alias``.

    Ephemeral uploads do NOT appear here — this lists durable storage only.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    file_id: str
    filename: str = Field(alias="file_name")
    size: int = Field(alias="file_size", ge=0)
    content_type: str = Field(alias="mime_type")
    file_extension: str
    created_at: datetime


class StorageUsage(BaseModel):
    """Durable-storage usage against your tier's free quota (bytes)."""

    model_config = ConfigDict(frozen=True)

    used_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    over_quota: bool


class FileList(BaseModel):
    """Your durable stored files plus a storage-usage summary.

    Returned by :py:meth:`convilyn.resources.files.AsyncFiles.list`.
    """

    model_config = ConfigDict(frozen=True)

    files: list[StoredFile]
    usage: StorageUsage
