"""User configuration of moe.

To avoid namespace confusion when using a variable named config, typical usage of this
module should just import the Config class directly::

    from moe.config import Config
    config = Config()

This class shouldn't be accessed normally by a plugin, it should instead be passed a
Config object through a hook.
"""

import functools
import importlib
import importlib.util
import logging
import os
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, List, Optional

import dynaconf
import pluggy
import sqlalchemy as sa
import sqlalchemy.orm

import alembic.command
import alembic.config
import moe
from moe.library.lib_item import LibItem

session_factory = sa.orm.sessionmaker()
MoeSession = sa.orm.scoped_session(session_factory)

__all__ = ["Config", "Hooks"]

log = logging.getLogger("moe.config")

DEFAULT_PLUGINS = (
    "add",
    "cli",
    "edit",
    "import",
    "info",
    "list",
    "move",
    "musicbrainz",
    "remove",
    "write",
)


class Hooks:
    """Config hooks."""

    @staticmethod
    @moe.hookspec
    def add_config_validator(settings: dynaconf.base.LazySettings):
        """Add a settings validator for the configuration file.

        Args:
            settings: Moe's settings.

        Example:
            Inside your hook implementation::

                settings.validators.register(
                    Validator("MOVE.LIBRARY_PATH", must_exist=True)
                )

        See https://www.dynaconf.com/validation/#validation for more info.
        """

    @staticmethod
    @moe.hookspec
    def add_hooks(plugin_manager: pluggy.manager.PluginManager):
        """Add hookspecs to be registered to Moe.

        Args:
            plugin_manager: PluginManager that registers the hookspec.

        Example:
            Inside your hook implementation::

                from moe.plugins.add import Hooks  # noqa: WPS433, WPS442
                plugin_manager.add_hookspecs(Hooks)
        """

    @staticmethod
    @moe.hookspec
    def edit_new_items(config: "Config", items: List[LibItem]):
        """Edit any new or changed items prior to them being added to the library.

        Args:
            config: Moe config.
            items: Any new or changed items in the current session. The items and
                their changes have not yet been committed to the library.

        See Also:
            The :meth:`process_new_items` hook if you wish to process any items after
            any final edits have been made and they have been successfully added to
            the library.
        """

    @staticmethod
    @moe.hookspec
    def process_new_items(config: "Config", items: List[LibItem]):
        """Process any new or changed items after they have been added to the library.

        Args:
            config: Moe config.
            items: Any new or changed items that have been successfully added to the
                library during the current session.

        See Also:
            The :meth:`edit_new_items` hook if you wish to edit the items.
        """

    @staticmethod
    @moe.hookspec
    def plugin_registration(
        config: "Config", plugin_manager: pluggy.manager.PluginManager
    ):
        """Allows actions after the initial plugin registration.

        In order for a module to implement and register plugin hooks, it must be
        registered as a separate plugin with the ``plugin_manager``. A plugin can be
        either just a module, or a full package.

        If a plugin is a package, only it's ``__init__.py`` will be initially
        registered, meaning only ``__init__.py`` will be able to run hook
        implementations at start-up. This hook is provided so each plugin can register
        it's individual sub-modules as appropriate.

        Important:
            Ensure any sub-modules you register as plugins are registered with the
            original plugin name as the prefix. This helps prevent naming conflicts.

        For example, see how the ``edit`` plugin conditionally enables its cli
        sub-module::

            @moe.hookimpl
            def plugin_registration(config, plugin_manager):
                if "cli" in config.plugins:
                    plugin_manager.register(edit_cli, "edit_cli")

        This hook can also be used as a way of checking for plugin dependencies by
        inspecting the enabled plugins in the configuration.

        For example, because the ``list`` plugin only exists as a cli plugin, it will
        un-register itself and log a warning if the cli plugin is not enabled::

            @moe.hookimpl
            def plugin_registration(config, plugin_manager):
                if "cli" not in config.plugins:
                    plugin_manager.set_blocked("list")
                    log.warning("You can't list stuff without a cli!")

        See Also:
            The ``PluginManager`` api documentation:
            https://pluggy.readthedocs.io/en/latest/api_reference.html

        Args:
            config: Moe config.
            plugin_manager: Plugin manager used to operate on plugins.
        """


@moe.hookimpl
def add_config_validator(settings: dynaconf.base.LazySettings):
    """Validate move plugin configuration settings."""
    settings.validators.register(
        dynaconf.Validator("DEFAULT_PLUGINS", default=list(DEFAULT_PLUGINS))
    )


class Config:
    """Initializes moe configuration settings and database.

    Note:
        `init_db()` is not included in `__init__()` for testing purposes.

    Attributes:
        config_dir (Path): Filesystem path of the configuration directory.
        config_file (Path): Filesystem path of the configuration settings file.
        engine (sa.engine.base.Engine): Database engine in use.
        plugin_manager (pluggy.manager.PluginManager): Manages plugin logic.
        plugins (List[str]): Enabled plugins.
        settings (dynaconf.base.LazySettings): User configuration settings.

    Example:
        In your plugin, to access the library_path setting (assuming a Config object
        named config)::

            config.settings.library_path

        See the dynaconf documentation for more info on reading settings variables.
        https://www.dynaconf.com/#reading-settings-variables
    """

    def __init__(
        self,
        config_dir: Path = Path.home() / ".config" / "moe",
        settings_filename: str = "config.toml",
        engine: Optional[sa.engine.base.Engine] = None,
        init_db=True,
    ):
        """Initializes the plugin manager and configuration directory.

        Args:
            config_dir: Filesystem path of the configuration directory where the
                settings and database files will reside. The environment variable
                ``MOE_CONFIG_DIR`` has precedence in setting this.
            settings_filename: Name of the configuration settings file.
            engine: sqlalchemy database engine to use. Defaults to a sqlite db located
                in the ``config_dir``.
            init_db: Whether or not to initialize the database.
        """
        try:
            self.config_dir = Path(os.environ["MOE_CONFIG_DIR"])
        except KeyError:
            self.config_dir = config_dir
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.config_file = self.config_dir / settings_filename
        self._read_config()

        self.engine = engine
        if init_db:
            self._init_db()

    def _init_db(self, create_tables: bool = True):
        """Initializes the database.

        Moe uses sqlite by default.

        Args:
            create_tables: Whether or not to create and update the db tables.
                If doing db migrations manually, e.g. in alembic, this shuold be False.
        """
        db_path = self.config_dir / "library.db"

        if not self.engine:
            self.engine = sa.create_engine("sqlite:///" + str(db_path))

        session_factory.configure(bind=self.engine)

        # create and update database tables
        if create_tables:
            config_path = Path(__file__)
            alembic_cfg = alembic.config.Config(
                config_path.parents[2] / "alembic" / "alembic.ini"
            )
            alembic_cfg.attributes["configure_logger"] = False
            with self.engine.begin() as connection:
                alembic_cfg.attributes["connection"] = connection
                alembic.command.upgrade(alembic_cfg, "head")

        # initialize sqlalchemy event listeners
        session = MoeSession()
        sqlalchemy.event.listen(
            session,
            "before_flush",
            functools.partial(_edit_new_items, config=self),
        )
        sqlalchemy.event.listen(
            session,
            "after_flush",
            functools.partial(_process_new_items, config=self),
        )

        # create regular expression function for sqlite queries
        @sa.event.listens_for(self.engine, "begin")  # noqa: WPS430
        def sqlite_engine_connect(conn):  # noqa: WPS430
            if (sys.version_info.major, sys.version_info.minor) < (3, 8):
                conn.connection.create_function("regexp", 2, _regexp)
            else:
                conn.connection.create_function(
                    "regexp", 2, _regexp, deterministic=True
                )

        def _regexp(pattern: str, col_value) -> bool:  # noqa: WPS430
            """Use the python re module for sqlite regular expression functionality.

            Args:
                pattern: Regular expression pattern.
                col_value: Column value to match against. The match will be against
                    the str of the value.

            Returns:
                Whether or not the match was successful.
            """
            return re.search(pattern, str(col_value), re.IGNORECASE) is not None

    def _read_config(self):
        """Reads the user configuration settings.

        Searches for a configuration file at `config_dir / "config.toml"`.

        Raises:
            SystemExit: No config file found.
        """
        self.config_file.touch(exist_ok=True)

        self.settings = dynaconf.Dynaconf(
            envvar_prefix="MOE",  # export envvars with `export MOE_FOO=bar`
            settings_file=str(self.config_file.resolve()),
        )

        self._setup_plugins()

        self.plugin_manager.hook.add_config_validator(settings=self.settings)
        self.settings.validators.validate()

    def _setup_plugins(self):
        """Setup plugin_manager and hook logic."""
        self.plugin_manager = pluggy.PluginManager("moe")

        # need to validate `config` specific settings separately so we have access to
        # the 'default_plugins' setting
        self.plugin_manager.register(moe.config, name="config")
        self.plugin_manager.add_hookspecs(Hooks)
        self.plugin_manager.hook.add_config_validator(settings=self.settings)
        self.settings.validators.validate()
        self.plugins = self.settings.default_plugins

        # the 'import' plugin maps to the 'moe_import' package
        with suppress(ValueError):
            import_index = self.plugins.index("import")
            self.plugins[import_index] = "moe_import"

        if "cli" in self.plugins:
            self.plugin_manager.register(importlib.import_module("moe.cli"), name="cli")

        # register plugin hookimpls for all enabled plugins
        self._register_internal_plugins()

        # register individual plugin sub-modules
        self.plugin_manager.hook.plugin_registration(
            config=self, plugin_manager=self.plugin_manager
        )

        # register plugin hookspecs for all enabled plugins
        self.plugin_manager.hook.add_hooks(plugin_manager=self.plugin_manager)

    def _register_internal_plugins(self):
        """Registers internal Moe plugins.

        Only registers plugins that are enabled in the configuration.
        """
        plugin_dir = Path(__file__).resolve().parent / "plugins"

        for plugin_path in plugin_dir.iterdir():
            plugin_name = plugin_path.stem

            if plugin_path.stem in self.plugins:
                plugin = importlib.import_module("moe.plugins." + plugin_name)
                self.plugin_manager.register(plugin, plugin_name)


def _edit_new_items(
    session: sa.orm.Session,
    flush_context: sa.orm.UOWTransaction,
    instances: Optional[Any],
    config: Config,
):
    """Runs the ``edit_new_items`` hook specification.

    This uses the sqlalchemy ORM event ``before_flush`` in the background to determine
    the time of execution and to provide any new or changed items to the hook
    implementations.

    Args:
        session: Current db session.
        flush_context: sqlalchemy obj which handles the details of the flush.
        instances: List of objects passed to the ``flush()`` method.
        config: Moe config.
    """
    config.plugin_manager.hook.edit_new_items(
        config=config, items=session.new.union(session.dirty)
    )


def _process_new_items(
    session: sa.orm.Session,
    flush_context: sa.orm.UOWTransaction,
    config: Config,
):
    """Runs the ``process_new_items`` hook specification.

    This uses the sqlalchemy ORM event ``after_flush`` in the background to determine
    the time of execution and to provide any new or changed items to the hook
    implementations.

    Args:
        session: Current db session.
        flush_context: sqlalchemy obj which handles the details of the flush.
        config: Moe config.
    """
    config.plugin_manager.hook.process_new_items(
        config=config, items=session.new.union(session.dirty)
    )
