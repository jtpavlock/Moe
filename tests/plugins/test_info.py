"""Tests the ``info`` plugin."""

from types import FunctionType
from typing import Iterator
from unittest.mock import patch

import pytest

import moe.cli
from tests.conftest import album_factory, extra_factory, track_factory


@pytest.fixture
def mock_query() -> Iterator[FunctionType]:
    """Mock a database query call.

    Use ``mock_query.return_value` to set the return value of a query.

    Yields:
        Mock query
    """
    with patch("moe.plugins.info.cli_query", autospec=True) as mock_query:
        yield mock_query


@pytest.fixture
def _tmp_info_config(tmp_config):
    """A temporary config for the info plugin with the cli."""
    tmp_config('default_plugins = ["cli", "info"]')


@pytest.mark.usefixtures("_tmp_info_config")
class TestCommand:
    """Test the plugin argument parser.

    To see the actual ouput of any of the tests, comment out
    `assert capsys.readouterr().out` and add `assert 0` to the end of the test.
    """

    def test_track(self, capsys, mock_query):
        """Tracks are printed to stdout with valid query."""
        track = track_factory()
        cli_args = ["info", "*"]
        mock_query.return_value = [track]

        moe.cli.main(cli_args)

        mock_query.assert_called_once_with("*", query_type="track")
        assert capsys.readouterr().out

    def test_album(self, capsys, mock_query):
        """Albums are printed to stdout with valid query."""
        album = album_factory()
        cli_args = ["info", "-a", "*"]
        mock_query.return_value = [album]

        moe.cli.main(cli_args)

        mock_query.assert_called_once_with("*", query_type="album")
        assert capsys.readouterr().out

    def test_extra(self, capsys, mock_query):
        """Extras are printed to stdout with valid query."""
        extra = extra_factory()
        cli_args = ["info", "-e", "*"]
        mock_query.return_value = [extra]

        moe.cli.main(cli_args)

        mock_query.assert_called_once_with("*", query_type="extra")
        assert capsys.readouterr().out

    def test_multiple_items(self, capsys, mock_query):
        """All items returned from the query are printed."""
        cli_args = ["info", "*"]
        mock_query.return_value = [track_factory(), track_factory()]

        moe.cli.main(cli_args)

        assert capsys.readouterr().out


class TestPluginRegistration:
    """Test the `plugin_registration` hook implementation."""

    def test_no_cli(self, tmp_config):
        """Don't enable the info cli plugin if the `cli` plugin is not enabled."""
        config = tmp_config(settings='default_plugins = ["info"]')

        assert not config.pm.has_plugin("info")

    def test_cli(self, tmp_config):
        """Enable the info cli plugin if the `cli` plugin is enabled."""
        config = tmp_config(settings='default_plugins = ["info", "cli"]')

        assert config.pm.has_plugin("info")
