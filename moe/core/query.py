"""Provides a positional commandline argument for querying the database.

Plugins wishing to use the query argument should define query as a parent parser
when using the add_command() hook.

Example:
    Inside your `add_command` hook implemention::

        my_parser = cmd_parser.add_parser("my_plugin", parents=[query.query_parser])

Then, in your argument parsing function, call `query.query(args.query)`
to get a list of Tracks matching the query from the library.
"""

import argparse
import logging
import re
import shlex
from typing import Dict, List

import sqlalchemy

from moe.core.library.album import Album
from moe.core.library.lib_item import LibItem
from moe.core.library.track import Track

log = logging.getLogger(__name__)

query_parser = argparse.ArgumentParser(
    add_help=False, formatter_class=argparse.RawTextHelpFormatter
)
query_parser.add_argument("query", help="query the library for matching tracks")
query_parser.add_argument(
    "-a", "--album", action="store_true", help="query albums instead of tracks"
)


HELP_STR = r"""
The query must be in the format 'field:value' where field is a track or album's field to
match and value is that field's value. Internally, this 'field:value' pair is referred
to as a single term. The match is case-insensitive.

If you would like to specify a value with whitespace or multiple words, enclose the
term in quotes.

SQL LIKE query syntax is used for normal queries, which means
the '_'  and '%' characters have special meaning:
% - The percent sign represents zero, one, or multiple characters.
_ - The underscore represents a single character.

To match these special characters as normal, use '/' as an escape character.

The value can also be a regular expression. To enforce this, use two colons
e.g. 'field::value.*'

As a shortcut to matching all entries, use '*' as the term.

Finally, you can also specify any number of terms.
For example, to match all Wu-Tang Clan tracks that start with the letter 'A', use:
'"artist:wu-tang clan" title:a%'

Note that when using multiple terms, they are joined together using AND logic, meaning
all terms must be true to return a match.

If doing an album query, you still specify track fields, but it will match albums
instead of tracks.

Tip: Normal queries may be faster when compared to regex queries. If you
are experiencing performance issues with regex queries, see if you can make an
equivalent normal query using the LIKE wildcard characters.
"""  # noqa: WPS360

# each query will be split into these groups
FIELD_GROUP = "field"
SEPARATOR_GROUP = "separator"
VALUE_GROUP = "value"


def query(
    query_str: str, session: sqlalchemy.orm.session.Session, album_query: bool = False
) -> List[LibItem]:
    """Queries the database for the given query string.

    Args:
        query_str: Query string to parse. See HELP_STR for more info.
        album_query: Whether or not to match albums instead of tracks.
        session: current db session

    Returns:
        All tracks matching the query.
    """
    terms = shlex.split(query_str)

    if not terms:
        log.error(f"No query given.\n{HELP_STR}")
        return []

    if album_query:
        library_query = session.query(Album).join(Track)
    else:
        library_query = session.query(Track).join(Album)

    for term in terms:
        try:
            library_query = library_query.filter(_create_expression(_parse_term(term)))
        except ValueError as exc:
            log.error(exc)
            return []

    items = library_query.all()

    if not items:
        log.warning(f"No items found for the query '{query_str}'.")

    return items


def _parse_term(term: str) -> Dict[str, str]:
    """Parse the given database query term.

    A term is a single field:value declaration.

    Args:
        term: Query string to parse.

    Returns:
        A dictionary containing each named group and its value.
        The named groups are field, separator, and value.

    Example:
        >>> parse_term('artist:name')
        {"field": "artist", "separator": ":", "value": "name"}

    Note:
        The fields are meant to be programatically accessed with the respective
        group constant e.g. `expression[FIELD_GROUP] == "artist"`

    Raises:
        ValueError: Invalid query term.
    """
    # '*' is used as a shortcut to return all entries.
    # We use track_num as all tracks are guaranteed to have a track number.
    # '%' is an SQL LIKE query special character.
    if term == "*":
        return {FIELD_GROUP: "track_num", SEPARATOR_GROUP: ":", VALUE_GROUP: "%"}

    query_re = re.compile(
        rf"""
        (?P<{FIELD_GROUP}>\S+?)
        (?P<{SEPARATOR_GROUP}>::?)
        (?P<{VALUE_GROUP}>\S.*)
        """,
        re.VERBOSE,
    )

    match = re.match(query_re, term)
    if not match:
        raise ValueError(f"Invalid query term: {term}\n{HELP_STR}")

    match_dict = match.groupdict()
    match_dict[FIELD_GROUP] = match_dict[FIELD_GROUP].lower()

    return match_dict


def _create_expression(term: Dict[str, str]) -> sqlalchemy.sql.elements.ClauseElement:
    """Maps a user-given query term to a filter expression for the database query.

    Args:
        term: A parsed query term defined by `_parse_term()`.

    Returns:
        A filter for the database query.

        A "filter" is anything accepted by a sqlalchemy `Query.filter()`.
        https://docs.sqlalchemy.org/en/13/orm/query.html#sqlalchemy.orm.query.Query.filter

    Raises:
        ValueError: Invalid query given.
    """
    field = term[FIELD_GROUP].lower()
    separator = term[SEPARATOR_GROUP]
    value = term[VALUE_GROUP]

    attr = Track.get_attr(field)

    if separator == ":":
        # Normal string match query - should be case insensitive.
        return attr.ilike(sqlalchemy.sql.expression.literal(value), escape="/")

    elif separator == "::":
        # Regular expression query.
        # Note, this is a custom sqlite function created in config.py
        try:
            re.compile(value)
        except re.error:
            raise ValueError(f"Invalid regular expression: {value}")

        return attr.op("regexp")(sqlalchemy.sql.expression.literal(value))

    raise ValueError(f"Invalid query type: {separator}")
