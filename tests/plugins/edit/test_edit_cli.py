"""Tests the cli ``edit`` plugin."""

import argparse
from unittest.mock import Mock, patch

import pytest

import moe
from moe.config import MoeSession
from moe.library.track import Track
from moe.plugins.edit import edit_cli


class TestParseArgs:
    """Test general functionality of the argument parser."""

    def test_multiple_items(self, mock_track_factory):
        """All items returned from a query are edited."""
        args = argparse.Namespace(
            fv_terms=["track_num=3"], query="", album=False, extra=False
        )

        track1 = mock_track_factory()
        track2 = mock_track_factory()
        with patch("moe.query.query", return_value=[track1, track2]) as mock_query:
            edit_cli._parse_args(config=Mock(), args=args)

            mock_query.assert_called_once_with("", query_type="track")

        assert track1.track_num == 3
        assert track2.track_num == 3

    def test_multiple_terms(self, mock_track):
        """We can edit multiple terms at once."""
        args = argparse.Namespace(
            fv_terms=["track_num=3", "title=yo"], query="", album=False, extra=False
        )

        with patch("moe.query.query", return_value=[mock_track]) as mock_query:
            edit_cli._parse_args(config=Mock(), args=args)

            mock_query.assert_called_with("", query_type="track")

        assert mock_track.track_num == 3
        assert mock_track.title == "yo"

    def test_invalid_fv_term(self, mock_track):
        """Raise SystemExit if field/value term is in the wrong format."""
        args = argparse.Namespace(
            fv_terms=["bad_format"], query="", album=False, extra=False
        )

        with pytest.raises(SystemExit) as error:
            with patch("moe.query.query", return_value=[mock_track]):
                edit_cli._parse_args(config=Mock(), args=args)

        assert error.value.code != 0

    def test_single_invalid_field(self, mock_track):
        """If only one out of multiple fields are invalid, still process the others."""
        args = argparse.Namespace(
            fv_terms=["lol=3", "track_num=3"], query="", album=False, extra=False
        )

        with pytest.raises(SystemExit) as error:
            with patch("moe.query.query", return_value=[mock_track]) as mock_query:
                edit_cli._parse_args(config=Mock(), args=args)

                mock_query.assert_called_once_with("", query_type="track")

        assert error.value.code != 0
        assert mock_track.track_num == 3


@pytest.mark.integration
class TestCommand:
    """Test cli integration with the `edit` command."""


def test_parse_args(real_track, tmp_config):
    """Music is edited when the `edit` command is invoked."""
    new_title = "Lovely Day"
    assert real_track.title != new_title
    cli_args = ["edit", "*", f"title={new_title}"]

    config = tmp_config(settings='default_plugins = ["cli", "edit"]', init_db=True)

    session = MoeSession()
    with session.begin():
        session.add(real_track)

    moe.cli.main(cli_args, config)

    with session.begin():
        edited_track = session.query(Track).one()

        assert edited_track.title == new_title
