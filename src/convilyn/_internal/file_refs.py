"""Normalise a caller's file references onto the one form the wire takes: ids.

``files.upload()`` returns a :class:`~convilyn.types.File`, and
``convert.create(file=...)`` takes that object -- so handing the same object to
``goals.understand(files=[...])`` is the obvious next move. It produced::

    TypeError: Object of type File is not JSON serializable

raised from inside ``httpx`` while encoding the request body, several frames
below any convilyn code, naming neither ``understand`` nor ``file_id`` nor what
to pass instead. The same object; one resource took it, the other did not.

**Why the two resources differ, and why the difference stops here.** ``convert``
handles ONE file and can afford two parameters -- ``file=`` for the object,
``file_id=`` for the id -- with a mutual-exclusion check between them.
``goals`` takes a LIST and has one parameter, so the same split is not
available: a list is where both forms have to meet. This module is that meeting
point, and every place ``goals`` builds ``fileIds`` goes through it.

**``File.__str__`` is NOT the fix**, recorded here because it is the natural one
to reach for and it does not work: :func:`json.dumps` does not call ``__str__``
on an object it does not recognise, it raises. Unwrapping at the boundary is the
only thing that changes the outcome.

The refusal message follows the house shape set by
``convert.py``'s ``_resolve_source`` -- name what was expected, name the way to
get it, and echo the type that arrived -- because that message exists for the
mirror-image mistake (a ``str`` where a ``File`` was wanted), and a caller who
makes one of the two is well placed to make the other.
"""

from __future__ import annotations

from collections.abc import Iterable

from convilyn.types import File


def file_ids(values: Iterable[str | File]) -> list[str]:
    """Return ``values`` as a list of file-id strings.

    Accepts an id string or an uploaded :class:`~convilyn.types.File`, in any
    mix -- a caller holding some of each should not have to normalise them
    itself, which is the whole reason this exists.

    Raises:
        TypeError: an element is neither. Raised HERE, where the parameter that
            was misused is still in the frame, rather than in the JSON encoder
            two layers down.
    """
    resolved: list[str] = []
    for value in values:
        if isinstance(value, File):
            resolved.append(value.file_id)
        elif isinstance(value, str):
            resolved.append(value)
        else:
            raise TypeError(
                "files= expects file ids or uploaded File objects; got "
                f"{type(value).__name__}. Upload first with files.upload(path), "
                "then pass the returned File or its .file_id"
            )
    return resolved
