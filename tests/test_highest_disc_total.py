"""The Highest Disc Total plugin, loaded and enabled the way Picard does, against Picard's Album/Track/File."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from picardtestcase import PicardTestCase

PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"
PLUGIN_ID = "highest-disc-total"


class HighestDiscTotalTest(PicardTestCase):
    def setUp(self):
        from picard.extension_points import set_plugin_uuid, unset_plugin_uuid
        from picard.plugin3.plugin import Plugin

        super().setUp()
        self.patch_tagger_instance("picard.item")
        # File.move reaches these; the test tagger doesn't have them.
        self.tagger._acoustid = self.tagger.acoustidmanager = (
            self.tagger.isrc_submit_manager
        ) = MagicMock()
        plugin = Plugin(PLUGINS_DIR, PLUGIN_ID)
        plugin.read_manifest()
        self.set_config_values(
            setting={
                "clear_existing_tags": False,
                "enable_tagger_scripts": False,
                "list_of_scripts": [],
                "preserved_tags": [],
                "plugins3_enabled_plugins": [plugin.manifest.uuid],
            }
        )
        plugin.load_module()
        plugin.enable(self.tagger)
        set_plugin_uuid(plugin.manifest.uuid, PLUGIN_ID)
        self.addCleanup(unset_plugin_uuid, plugin.manifest.uuid)
        self.addCleanup(plugin.disable)
        self.plugin = plugin

    def make_album(self, tracks_per_disc, album_id="album-id"):
        from picard.album import Album
        from picard.track import Track

        album = Album(album_id)
        total = str(len(tracks_per_disc))
        for disc, count in enumerate(tracks_per_disc, 1):
            for n in range(1, count + 1):
                track = Track(f"d{disc}t{n}", album)
                track.metadata["discnumber"] = str(disc)
                track.metadata["totaldiscs"] = total
                track.metadata["tracknumber"] = str(n)
                track.orig_metadata.copy(track.metadata)
                track.scripted_metadata.update(track.metadata)
                album.tracks.append(track)
        return album

    def track(self, album, disc, n=1):
        return next(t for t in album.tracks if t.id == f"d{disc}t{n}")

    def set_mb(self, track, tag, value):
        # As if MusicBrainz (and tagger scripts) had given the track this value.
        for metadata in (track.metadata, track.orig_metadata, track.scripted_metadata):
            metadata[tag] = value

    def match(self, album, disc, n=1, file_tags=None):
        from picard.file import File

        file = File(f"C:/music/{album.id}-d{disc}t{n}.mp3")
        file.orig_metadata.update(file_tags or {})
        file.metadata.copy(file.orig_metadata)
        return self.match_file(album, file, disc, n)

    def match_file(self, album, file, disc, n=1):
        track = self.track(album, disc, n)
        file.parent_item = track
        track.add_file(file)
        return file

    def totals(self, album):
        return sorted({f.metadata["totaldiscs"] for t in album.tracks for f in t.files})

    def numbering(self, *files):
        return [f"{f.metadata['discnumber']}/{f.metadata['totaldiscs']}" for f in files]

    def test_manifest_valid_for_this_picard(self):
        from picard import api_versions_tuple

        manifest = self.plugin.manifest
        assert manifest.validate() == []
        assert set(manifest.api_versions) & set(api_versions_tuple)
        assert manifest.license == "GPL-3.0-or-later"

    def test_missing_last_disc_lowers_total(self):
        album = self.make_album([16, 12])  # CD + DVD
        for n in range(1, 17):
            self.match(album, 1, n)
        assert self.totals(album) == ["1"]

    def test_matching_later_disc_restores_total_and_refreshes_files(self):
        album = self.make_album([3, 3])
        f1 = self.match(album, 1)
        assert self.totals(album) == ["1"]
        with patch.object(f1, "update") as update:
            self.match(album, 2)
        assert self.totals(album) == ["2"]
        update.assert_called_once()

    def test_discs_matched_out_of_order(self):
        album = self.make_album([2, 2, 2])
        self.match(album, 3)
        self.match(album, 1)
        self.match(album, 2)
        assert self.totals(album) == ["3"]

    def test_removing_last_disc_file_lowers_total_again(self):
        album = self.make_album([3, 3])
        self.match(album, 1)
        f2 = self.match(album, 2)
        self.track(album, 2).remove_file(f2)
        assert self.totals(album) == ["1"]

    def test_moving_file_to_an_earlier_disc_lowers_total(self):
        album = self.make_album([3, 3])
        self.match(album, 1)
        f2 = self.match(album, 2)
        f2.move(self.track(album, 1, 2))
        assert self.totals(album) == ["1"]

    def test_moving_file_between_albums(self):
        a, b = self.make_album([2, 2], "a"), self.make_album([3, 3, 3], "b")
        self.match(a, 1)
        fa2 = self.match(a, 2)
        self.match(b, 1)
        self.match(b, 2)
        fa2.move(self.track(b, 3))
        assert (self.totals(a), self.totals(b)) == (["1"], ["3"])

    def test_gap_in_middle_closed(self):
        album = self.make_album([2, 2, 2, 2])
        f1, f3 = self.match(album, 1), self.match(album, 3)
        assert self.numbering(f1, f3) == ["1/2", "2/2"]

    def test_several_gaps_closed(self):
        album = self.make_album([1, 1, 1, 1, 1])
        f2, f5 = self.match(album, 2), self.match(album, 5)
        assert self.numbering(f2, f5) == ["1/2", "2/2"]

    def test_all_tracks_of_a_disc_renumbered(self):
        album = self.make_album([2, 2, 2])
        files = [self.match(album, 3, n) for n in (1, 2)]
        assert self.numbering(*files) == ["1/1", "1/1"]

    def test_matching_missing_disc_restores_numbering_and_refreshes_files(self):
        album = self.make_album([2, 2, 2])
        f1, f3 = self.match(album, 1), self.match(album, 3)
        with patch.object(f3, "update") as update:
            f2 = self.match(album, 2)
        assert self.numbering(f1, f2, f3) == ["1/3", "2/3", "3/3"]
        update.assert_called_once()

    def test_removing_middle_disc_renumbers_later_discs(self):
        album = self.make_album([2, 2, 2])
        f1, f2, f3 = self.match(album, 1), self.match(album, 2), self.match(album, 3)
        self.track(album, 2).remove_file(f2)
        assert self.numbering(f1, f3) == ["1/2", "2/2"]

    def test_edited_disc_number_kept(self):
        album = self.make_album([2, 2, 2])
        self.match(album, 1)
        f3 = self.match(album, 3)
        f3.metadata["discnumber"] = "9"
        self.match(album, 2)
        assert self.numbering(f3) == ["9/3"]

    def test_preserved_disc_number_kept(self):
        from picard.file import run_file_pre_save_processors

        self.set_config_values(setting={"preserved_tags": ["discnumber"]})
        album = self.make_album([2, 2, 2])
        self.match(album, 1)
        f3 = self.match(album, 3, file_tags={"discnumber": "3"})
        run_file_pre_save_processors(f3)
        assert self.numbering(f3) == ["3/3"]  # the total stays at the kept disc number

    def test_pre_save_renumbers_disc_reset_without_the_add_hook(self):
        from picard.file import run_file_pre_save_processors

        uuid = self.plugin.manifest.uuid
        self.set_config_values(setting={"plugins3_enabled_plugins": []})
        album = self.make_album([2, 2, 2])
        self.match(album, 1)
        f3 = self.match(album, 3)  # matched before the plugin was enabled
        self.set_config_values(setting={"plugins3_enabled_plugins": [uuid]})
        run_file_pre_save_processors(f3)
        assert self.numbering(f3) == ["2/2"]

    def test_disc_edit_on_track_row_kept(self):
        from picard.file import run_file_pre_save_processors
        from picard.ui.metadatabox import apply_tag_values

        album = self.make_album([2, 2, 2])
        f1 = self.match(album, 1)  # disc 1 stays disc 1, so the plugin never writes it
        f3 = self.match(album, 3)
        list(apply_tag_values([self.track(album, 1), f1], "discnumber", ["5"]))
        run_file_pre_save_processors(f1)
        run_file_pre_save_processors(f3)
        # The total stays above the edited disc, up to MusicBrainz's; disc 3 still follows disc 1.
        assert self.numbering(f1, f3) == ["5/3", "2/2"]

    def test_musicbrainz_disc_number_typed_back_is_renumbered_again(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([2, 2, 2])
        self.match(album, 1)
        f3 = self.match(album, 3)
        f3.metadata["discnumber"] = (
            "3"  # indistinguishable from a reset; Preserved Tags pins it
        )
        run_file_pre_save_processors(f3)
        assert self.numbering(f3) == ["2/2"]

    def test_padded_numbers_kept_when_no_disc_missing(self):
        album = self.make_album([1, 1])
        for t in album.tracks:
            self.set_mb(
                t, "discnumber", f"0{t.metadata['discnumber']}"
            )  # e.g. $num(%discnumber%,2)
            self.set_mb(t, "totaldiscs", "02")
        f1, f2 = self.match(album, 1), self.match(album, 2)
        assert self.numbering(f1, f2) == ["01/02", "02/02"]

    def test_padded_numbers_keep_their_width(self):
        album = self.make_album([1, 1, 1])
        for t in album.tracks:
            self.set_mb(t, "discnumber", f"0{t.metadata['discnumber']}")
            self.set_mb(t, "totaldiscs", "03")
        f3 = self.match(album, 3)
        assert self.numbering(f3) == ["01/01"]

    def test_gap_closed_without_musicbrainz_total(self):
        album = self.make_album([2, 2, 2])
        for t in album.tracks:
            self.set_mb(t, "totaldiscs", "")
        f1, f3 = self.match(album, 1), self.match(album, 3)
        assert self.numbering(f1, f3) == ["1/", "2/"]

    def test_unusable_disc_number_beside_a_counted_disc(self):
        album = self.make_album([2, 2])
        self.set_mb(self.track(album, 1), "discnumber", "x")
        self.match(album, 2)
        with self.assertNoLogs("main", level="ERROR"):
            fx = self.match(album, 1)
        assert fx.metadata["discnumber"] == "x"

    def test_missing_last_of_three(self):
        album = self.make_album([2, 2, 2])
        self.match(album, 1)
        self.match(album, 2)
        assert self.totals(album) == ["2"]

    def test_each_file_capped_by_its_own_track_total(self):
        album = self.make_album([2, 2])
        for n in (1, 2):
            self.set_mb(self.track(album, 2, n), "totaldiscs", "1")
        f1, f2 = self.match(album, 1), self.match(album, 2)
        assert (f1.metadata["totaldiscs"], f2.metadata["totaldiscs"]) == ("2", "1")

    def test_never_raises_above_musicbrainz_total(self):
        album = self.make_album([2, 2])
        for t in album.tracks:  # MusicBrainz says 1 disc (e.g. edited), files on disc 2
            self.set_mb(t, "totaldiscs", "1")
        self.match(album, 2)
        assert self.totals(album) == ["1"]

    def test_unusable_disc_number_ignored(self):
        for discnumber in ("", "0", "x"):
            album = self.make_album([2, 2])
            self.set_mb(self.track(album, 1), "discnumber", discnumber)
            with self.assertNoLogs("main", level="ERROR"):
                self.match(album, 1)
            assert self.totals(album) == ["2"], discnumber

    def test_no_musicbrainz_total_left_alone(self):
        album = self.make_album([3, 3])
        for t in album.tracks:
            self.set_mb(t, "totaldiscs", "")
        with self.assertNoLogs("main", level="ERROR"):
            self.match(album, 1)
        assert self.totals(album) == [""]

    def test_files_already_correct_not_refreshed(self):
        album = self.make_album([3, 3])
        f1 = self.match(album, 1)
        with patch.object(f1, "update") as update:
            self.match(album, 1, 2)
        update.assert_not_called()

    def test_preserved_totaldiscs_kept(self):
        from picard.file import run_file_pre_save_processors

        self.set_config_values(setting={"preserved_tags": ["totaldiscs"]})
        album = self.make_album([3, 3])
        f1 = self.match(
            album, 1, file_tags={"totaldiscs": "2"}
        )  # MusicBrainz's value, so only preserving keeps it
        run_file_pre_save_processors(f1)
        assert f1.metadata["totaldiscs"] == "2"

    def test_edited_total_kept(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([3, 3])
        f1 = self.match(album, 1)
        f1.metadata["totaldiscs"] = "5"  # typed into the metadata box
        self.match(album, 2)
        run_file_pre_save_processors(f1)
        assert f1.metadata["totaldiscs"] == "5"

    def test_edited_total_kept_on_file_the_plugin_never_changed(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([3, 3])
        self.match(album, 1)
        f2 = self.match(album, 2)  # arrives with MusicBrainz's 2, already the target
        f2.metadata["totaldiscs"] = "4"
        run_file_pre_save_processors(f2)
        assert f2.metadata["totaldiscs"] == "4"

    def test_edit_on_track_row_kept(self):
        from picard.file import run_file_pre_save_processors
        from picard.ui.metadatabox import apply_tag_values

        album = self.make_album([3, 3])
        self.match(album, 1)
        f2 = self.match(
            album, 2
        )  # arrives with MusicBrainz's 2, so the plugin never writes it
        # A track with one file shows as one row; editing it writes the track and the file.
        list(apply_tag_values([self.track(album, 2), f2], "totaldiscs", ["5"]))
        run_file_pre_save_processors(f2)
        assert f2.metadata["totaldiscs"] == "5"

    def test_musicbrainz_total_typed_back_is_lowered_again(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([3, 3])
        f1 = self.match(album, 1)
        f1.metadata["totaldiscs"] = (
            "2"  # indistinguishable from a reset; Preserved Tags pins it
        )
        run_file_pre_save_processors(f1)
        assert f1.metadata["totaldiscs"] == "1"

    def test_deleted_total_stays_deleted(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([3, 3])
        f2 = self.match(
            album, 2
        )  # arrives with MusicBrainz's 2, the target: no marker yet
        f2.metadata.delete("totaldiscs")
        self.track(album, 2).remove_file(self.match(album, 2, 2))
        run_file_pre_save_processors(f2)
        assert f2.metadata["totaldiscs"] == ""

    def test_marker_carried_in_from_a_save_does_not_freeze_the_file(self):
        from picard.file import run_file_pre_save_processors

        uuid = self.plugin.manifest.uuid
        album = self.make_album([3, 3, 3])
        f1 = self.match(album, 1)
        assert f1.metadata["totaldiscs"] == "1"
        self.track(album, 1).remove_file(f1)
        f1.orig_metadata["~highest_disc_total_totaldiscs"] = (
            "1"  # saving copies ~ variables
        )
        self.set_config_values(setting={"plugins3_enabled_plugins": []})
        self.match_file(
            album, f1, 1
        )  # re-matched while the plugin is off: MusicBrainz's 3
        self.set_config_values(setting={"plugins3_enabled_plugins": [uuid]})
        f1.metadata["~highest_disc_total_totaldiscs"] = (
            "2"  # stale, from some earlier state
        )
        run_file_pre_save_processors(f1)
        assert f1.metadata["totaldiscs"] == "1"

    def test_edit_after_file_moved_kept(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([3, 3, 3])
        f1 = self.match(album, 1)
        assert f1.metadata["totaldiscs"] == "1"
        self.match(album, 2)
        f1.move(
            self.track(album, 3)
        )  # target is now MusicBrainz's 3, so nothing is written
        f1.metadata["totaldiscs"] = "1"  # the value the plugin wrote before the move
        run_file_pre_save_processors(f1)
        assert f1.metadata["totaldiscs"] == "1"

    def test_file_added_again_to_its_track(self):
        album = self.make_album([3, 3])
        f1 = self.match(album, 1)
        self.track(album, 1).add_file(
            f1
        )  # resets the file's tags to the track's, keeping ~ variables
        assert f1.metadata["totaldiscs"] == "1"

    def test_renumbered_file_added_again_to_its_track(self):
        album = self.make_album([2, 2, 2])
        f3 = self.match(album, 3)
        self.track(album, 3).add_file(f3)
        assert self.numbering(f3) == ["1/1"]

    def test_plugin_reload_still_follows_its_own_values(self):
        album = self.make_album([3, 3])
        f1 = self.match(album, 1)
        self.plugin.disable()  # also how a plugin update reloads it
        self.plugin.load_module()
        self.plugin.enable(self.tagger)
        self.match(album, 2)
        assert f1.metadata["totaldiscs"] == "2"

    def test_without_tagger_scripts_uses_musicbrainz_values(self):
        from picard.metadata import Metadata

        album = self.make_album([3, 3])
        for t in album.tracks:
            t.scripted_metadata = Metadata()  # Picard only fills it when scripts ran
        f1 = self.match(album, 1)
        assert f1.metadata["totaldiscs"] == "1"
        self.match(album, 2)
        assert self.totals(album) == ["2"]

    def test_total_set_by_plugin_still_follows_later_discs(self):
        album = self.make_album([2, 2, 2])
        f1 = self.match(album, 1)
        assert f1.metadata["totaldiscs"] == "1"
        self.match(album, 2)
        assert f1.metadata["totaldiscs"] == "2"
        self.match(album, 3)
        assert f1.metadata["totaldiscs"] == "3"

    def test_pre_save_fixes_total_reset_without_the_add_hook(self):
        from picard.file import run_file_pre_save_processors

        uuid = self.plugin.manifest.uuid
        self.set_config_values(setting={"plugins3_enabled_plugins": []})
        album = self.make_album([3, 3])
        f1 = self.match(album, 1)  # matched before the plugin was enabled
        assert f1.metadata["totaldiscs"] == "2"
        self.set_config_values(setting={"plugins3_enabled_plugins": [uuid]})
        run_file_pre_save_processors(f1)
        assert f1.metadata["totaldiscs"] == "1"

    def test_pre_save_ignores_unmatched_file(self):
        from picard.file import File, run_file_pre_save_processors

        f = File("C:/music/loose.mp3")
        f.metadata["totaldiscs"] = "2"
        with self.assertNoLogs("main", level="ERROR"):
            run_file_pre_save_processors(f)
        assert f.metadata["totaldiscs"] == "2"

    def test_pre_save_ignores_file_removed_from_its_track(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([3, 3])
        self.match(album, 1)
        f2 = self.match(album, 2)
        self.track(album, 2).remove_file(
            f2
        )  # parent_item still points at the track until the file is moved
        f2.metadata["totaldiscs"] = "2"
        with self.assertNoLogs("main", level="ERROR"):
            run_file_pre_save_processors(f2)
        assert f2.metadata["totaldiscs"] == "2"

    def test_pre_save_ignores_album_unmatched_files(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([3, 3, 3])
        self.match(album, 1)
        f = self.match(album, 2, file_tags={"totaldiscs": "2"})
        f.move(album.unmatched_files)
        album.unmatched_files.metadata["totaldiscs"] = (
            "2"  # set by editing Total Discs with the cluster selected
        )
        f.metadata["totaldiscs"] = "2"
        with self.assertNoLogs("main", level="ERROR"):
            run_file_pre_save_processors(f)
        assert f.metadata["totaldiscs"] == "2"

    def test_standalone_recordings_not_scanned(self):
        from picard.album import NatAlbum
        from picard.file import File, run_file_pre_save_processors
        from picard.track import Track

        album = NatAlbum()
        track = Track("rec-id", album)
        album.tracks.append(track)
        file = File("C:/music/rec.mp3")
        file.parent_item = track
        with patch.object(self.plugin._module, "discs_with_files") as scan:
            track.add_file(file)
            run_file_pre_save_processors(file)
            track.remove_file(file)
        scan.assert_not_called()

    def test_disabled_plugin_leaves_total(self):
        self.set_config_values(setting={"plugins3_enabled_plugins": []})
        album = self.make_album([3, 3])
        self.match(album, 1)
        assert self.totals(album) == ["2"]
