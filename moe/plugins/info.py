"""Prints information about music in the library.

All fields and their values should be printed to stdout for any music queried.
"""

import argparse
import types
from collections import OrderedDict
from typing import Any, Dict, List

import sqlalchemy

import moe
from moe.core import query
from moe.core.config import Config
from moe.core.library.album import Album
from moe.core.library.extra import Extra
from moe.core.library.lib_item import LibItem
from moe.core.library.track import Track

__all__: List[str] = []


@moe.hookimpl
def add_command(cmd_parsers: argparse._SubParsersAction):  # noqa: WPS437
    """Adds the ``info`` command to Moe's CLI."""
    add_parser = cmd_parsers.add_parser(
        "info",
        description="Prints information about music in the library.",
        help="print info for music in the library",
        parents=[query.query_parser],
    )
    add_parser.set_defaults(func=_parse_args)


def _parse_args(
    config: Config, session: sqlalchemy.orm.session.Session, args: argparse.Namespace
):
    """Parses the given commandline arguments.

    Args:
        config: Configuration in use.
        session: Current db session.
        args: Commandline arguments to parse.

    Raises:
        SystemExit: Query returned no tracks.
    """
    if args.album:
        query_type = "album"
    elif args.extra:
        query_type = "extra"
    else:
        query_type = "track"
    items = query.query(args.query, session, query_type=query_type)

    if not items:
        raise SystemExit(1)

    print(_fmt_infos(items), end="")  # noqa: WPS421


def _fmt_infos(items: List[LibItem]):
    """Formats information for multiple items together."""
    out_str = ""
    for item in items:
        out_str += _fmt_info(item)

        if item is not items[-1]:
            out_str += "\n"

    return out_str


def _fmt_info(item: LibItem) -> str:
    """Formats the attribute/value pairs of an item into a str."""
    if isinstance(item, Track):
        return _fmt_track_info(item)
    elif isinstance(item, Album):
        return _fmt_album_info(item)
    elif isinstance(item, Extra):
        return _fmt_extra_info(item)

    raise NotImplementedError


def _fmt_album_info(album: Album) -> str:
    """Formats an album's information for display.

    An album's information is represented by it's attributes (see `_get_base_dict()`)
    plus lists of its tracks and extras.

    Args:
        album: Album to format.

    Returns:
        Formatted string representing the album's relevant information to the user.
    """
    base_dict = _get_base_dict(album)
    base_dict.pop("extras")
    base_dict.pop("tracks")
    base_info = "\n".join(
        f"{field}: {value}"
        for field, value in OrderedDict(sorted(base_dict.items())).items()
    )

    tracks = "\nTracks\n"
    tracks += "\n".join(track.title for track in sorted(album.tracks))

    extras = "\nExtras\n"
    extras += "\n".join(extra.filename for extra in sorted(album.extras))

    return base_info + "\n" + tracks + "\n" + extras


def _fmt_extra_info(extra: Extra) -> str:
    """Formats a extra's information for display.

    An extra's information is represented by it's attributes (see `_get_base_dict()`).

    Args:
        extra: Extra to format.

    Returns:
        Formatted string representing the extra's relevant information to the user.
    """
    base_dict = _get_base_dict(extra)
    base_dict.pop("filename")

    return "\n".join(
        f"{field}: {value}"
        for field, value in OrderedDict(sorted(base_dict.items())).items()
    )


def _fmt_track_info(track: Track) -> str:
    """Formats a track's information for display.

    A track's information is represented by it's attributes (see `_get_base_dict()`).

    Args:
        track: Track to format.

    Returns:
        Formatted string representing the track's relevant information to the user.
    """
    base_dict = OrderedDict(sorted(_get_base_dict(track).items()))
    base_dict.pop("album_obj")
    base_dict.pop("genres")

    return "\n".join(
        f"{field}: {value}"
        for field, value in OrderedDict(sorted(base_dict.items())).items()
    )


def _get_base_dict(item: LibItem) -> Dict[str, Any]:
    """Represents an item as a dictionary.

    Only public attributes that are not empty will be included. Also, any attributes
    that are not relevant to the user e.g. sqlalchemy specific attributes will
    be removed.

    Args:
        item: Library item.

    Returns:
        Returns a dict representation of an Item in the form { attribute: value }.
    """
    item_dict = {}
    for attr in dir(item):  # noqa: WPS421
        if not attr.startswith("_") and attr != "metadata" and attr != "registry":
            value = getattr(item, attr)
            if (
                value
                and not isinstance(value, types.MethodType)
                and not isinstance(value, types.FunctionType)
            ):
                item_dict[attr] = value

    return item_dict
