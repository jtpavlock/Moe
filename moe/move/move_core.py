"""Core api for moving items."""

import logging
import re
import shutil
from contextlib import suppress
from pathlib import Path
from typing import Callable, Optional, Union

import dynaconf
import dynaconf.base
import pluggy
from unidecode import unidecode

import moe
from moe import config
from moe.library import Album, Extra, LibItem, Track

__all__ = ["copy_item", "fmt_item_path", "move_item"]

log = logging.getLogger("moe.move")


class Hooks:
    """Move plugin hook specifications."""

    @staticmethod
    @moe.hookspec
    def create_path_template_func() -> list[Callable]:  # type: ignore
        """Create a custom path template function.

        Any functions returned by this hook will be available to be called from within
        the path templates defined by the move plugin.

        Returns:
            A list of all custom path functions your plugin creates. The list should
            contain the callable functions themselves.
        """  # noqa: DAR202

    @staticmethod
    @moe.hookspec
    def override_album_path_config(album: Album) -> Optional[str]:  # type: ignore
        """Override the album path configuration.

        This hook allows plugins to modify the album path configuration dynamically
        based on the album's properties.

        Args:
            album: The Album object for which the path is being generated.

        Returns:
            An optional string representing the new album path configuration.
            If None is returned, the default configuration will be used.
        """

@moe.hookimpl
def add_hooks(pm: pluggy._manager.PluginManager):
    """Registers `add` hookspecs to Moe."""
    from moe.move.move_core import Hooks

    pm.add_hookspecs(Hooks)


@moe.hookimpl
def add_config_validator(settings: dynaconf.base.LazySettings):
    """Validate move plugin configuration settings."""
    default_album_path = "{album.artist}/{album.title} ({album.year})"
    default_extra_path = "{e_unique(extra)}"
    default_track_path = (
        "{f'Disc {track.disc:02}' if album.disc_total > 1 else ''}/"
        "{track.track_num:02} - {track.title}{track.path.suffix}"
    )

    validators = [
        dynaconf.Validator("MOVE.ASCIIFY_PATHS", default=False),
        dynaconf.Validator("MOVE.ALBUM_PATH", default=default_album_path),
        dynaconf.Validator("MOVE.EXTRA_PATH", default=default_extra_path),
        dynaconf.Validator("MOVE.TRACK_PATH", default=default_track_path),
    ]
    settings.validators.register(*validators)  # type: ignore


@moe.hookimpl(trylast=True)
def edit_new_items(items: list[LibItem]):
    """Copies and formats the path of an item after it has been added to the library."""
    for item in items:
        copy_item(item)


@moe.hookimpl
def create_path_template_func() -> list[Callable]:
    """Adds custom functions for the path templates."""
    return [e_unique]


def e_unique(extra: Extra) -> str:
    """Returns a unique filename for an extra within its album."""
    extra_names = [album_extra.path.name for album_extra in extra.album.extras]

    if (name_count := extra_names.count(extra.path.name)) > 1:
        return extra.path.stem + f" ({name_count - 1})" + extra.path.suffix

    return extra.path.name


########################################################################################
# Format paths
########################################################################################
def fmt_item_path(item: LibItem, parent: Optional[Path] = None) -> Path:
    """Returns a formatted item path according to the user configuration.

    Args:
        item: Item whose path will be formatted.
        parent: Optional path the formatted path will be relative to. By
            default, this will be according to the configuration path settings.

    Returns:
        A formatted path as defined by the ``{album/extra/track}_path`` config template
            settings relative to ``parent``.
    """
    log.debug(f"Formatting item path. [path={item.path}]")

    if isinstance(item, Album):
        parent = parent or Path(config.CONFIG.settings.library_path).expanduser()
        
        # Potentially override the album path config.
        override_result = config.CONFIG.pm.hook.override_album_path_config(album=item)
        album_path_config = override_result or config.CONFIG.settings.move.album_path
        
        # Ensure `album_path_config`` is a string
        if isinstance(album_path_config, list):
            album_path_config = album_path_config[0] if album_path_config else ""

        item_path = _eval_path_template(album_path_config, item)
    elif isinstance(item, Extra):
        parent = parent or fmt_item_path(item.album)
        item_path = _eval_path_template(config.CONFIG.settings.move.extra_path, item)
    else:
        assert isinstance(item, Track)
        parent = parent or fmt_item_path(item.album)
        item_path = _eval_path_template(config.CONFIG.settings.move.track_path, item)

    new_path = parent / item_path

    if config.CONFIG.settings.move.asciify_paths:
        new_path = Path(unidecode(str(new_path)))

    log.debug(f"Formatted item path. [path={new_path}]")
    return new_path


def _eval_path_template(template, lib_item) -> str:
    """Evaluates and sanitizes a path template.

    Args:
        template: Path template.
            See `_lazy_fstr_item()` for more info on accepted f-string templates.
        lib_item: Library item associated with the template.

    Returns:
        Evaluated path.
    """
    template_parts = template.split("/")
    sanitized_parts = []
    for template_part in template_parts:
        path_part = _lazy_fstr_item(template_part, lib_item)
        sanitized_part = _sanitize_path_part(path_part)
        if sanitized_part:
            sanitized_parts.append(sanitized_part)

    return "/".join(sanitized_parts)


def _lazy_fstr_item(template: str, lib_item: LibItem) -> str:
    """Evalutes the given f-string template for a specific library item.

    Args:
        template: f-string template to evaluate.
            All library items should have their own template and refer to variables as:
                Album: album (e.g. {album.title}, {album.artist})
                Track: track (e.g. {track.title}, {track.artist})
                Extra: extra (e.g. {extra.path.name}
        lib_item: Library item referenced in the template.


    Example:
        The default path template for an album is::

            {album.artist}/{album.title} ({album.year})

    Returns:
        Evaluated f-string.

    Raises:
        NotImplementedError: You discovered a new library item!
    """
    # add the appropriate library item to the scope
    if isinstance(lib_item, Album):
        album = lib_item  # noqa: F841
    elif isinstance(lib_item, Track):
        track = lib_item  # noqa: F841
        album = track.album
    elif isinstance(lib_item, Extra):
        extra = lib_item  # noqa: F841
        album = extra.album  # noqa: F841
    else:
        raise NotImplementedError

    plugin_funcs = config.CONFIG.pm.hook.create_path_template_func()
    for funcs in plugin_funcs:
        for func in funcs:
            globals()[func.__name__] = func

    return eval(f'f"""{template}"""')  # noqa: B907


def _sanitize_path_part(path_part: str) -> str:
    """Sanitizes a part of a path to be compatible with most filesystems.

    Note:
        Only sub-paths of the library path will be affected.

    Args:
        path_part: Path part to sanitize. Must be a single 'part' of a path, i.e. no /
            separators.

    Returns:
        Path part with all the replacements applied.
    """
    PATH_REPLACE_CHARS = {
        r"^\.": "_",  # leading '.' (hidden files on Unix)
        r'[<>:"\?\*\|\\/]': "_",  # <, >, : , ", ?, *, |, \, / (Windows reserved chars)
        r"\.$": "_",  # trailing '.' (Windows restriction)
        r"\s+$": "",  # trailing whitespace (Windows restriction)
    }

    for regex, replacement in PATH_REPLACE_CHARS.items():
        path_part = re.sub(regex, replacement, path_part)

    return path_part


########################################################################################
# Copy
########################################################################################
def copy_item(item: LibItem):
    """Copies an item to a destination as determined by the user configuration.

    Overwrites any existing files. Will create the destination if it does not already
    exist.
    """
    if isinstance(item, Album):
        _copy_album(item)
    elif isinstance(item, (Extra, Track)):
        _copy_file_item(item)


def _copy_album(album: Album):
    """Copies an album to a destination as determined by the user configuration."""
    dest = fmt_item_path(album)

    log.debug(f"Copying album. [{dest=}, {album=}]")

    dest.mkdir(parents=True, exist_ok=True)
    album.path = dest

    for track in album.tracks:
        _copy_file_item(track)

    for extra in album.extras:
        _copy_file_item(extra)

    log.info(f"Copied album. [{dest=!s}, {album=!s}]")


def _copy_file_item(item: Union[Extra, Track]):
    """Copies an extra or track to a destination as determined by the user config."""
    dest = fmt_item_path(item)
    if dest.exists() and dest.samefile(item.path):
        item.path = dest
        return

    log.debug(f"Copying item. [{dest=}, {item=}]")

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(item.path, dest)

    item.path = dest

    log.info(f"Copied item. [{dest=!s}, {item=!s}]")


########################################################################################
# Move
########################################################################################
def move_item(item: LibItem):
    """Moves an item to a destination as determined by the user configuration.

    Overwrites any existing files. Will create the destination if it does not already
    exist.
    """
    if isinstance(item, Album):
        _move_album(item)
    elif isinstance(item, (Extra, Track)):
        _move_file_item(item)


def _move_album(album: Album):
    """Moves an album to a given destination.

    Note:
        Empty leftover directories will be removed.
    """
    dest = fmt_item_path(album)
    old_album_dir = album.path

    log.debug(f"Moving album. [{dest=}, {album=}]")

    dest.mkdir(parents=True, exist_ok=True)
    album.path = dest

    for track in album.tracks:
        _move_file_item(track)

    for extra in album.extras:
        _move_file_item(extra)

    # remove any empty leftover directories
    for old_child in old_album_dir.rglob("*"):
        with suppress(OSError):
            old_child.rmdir()
    with suppress(OSError):
        old_album_dir.rmdir()
    for old_parent in old_album_dir.parents:
        with suppress(OSError):
            old_parent.rmdir()

    log.info(f"Moved album. [{dest=!s}, {album=!s}]")


def _move_file_item(item: Union[Extra, Track]):
    """Moves an extra or track to a destination as determined by the user config."""
    dest = fmt_item_path(item)
    if dest.exists() and dest.samefile(item.path):
        item.path = dest
        return

    log.debug(f"Moving item. [{dest=}, {item=}]")

    dest.parent.mkdir(parents=True, exist_ok=True)
    item.path.replace(dest)

    item.path = dest

    log.info(f"Moved item. [{dest=!s}, {item=!s}]")
