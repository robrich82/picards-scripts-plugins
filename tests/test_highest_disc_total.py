"""The Highest Disc Total plugin, loaded and enabled the way Picard does, against Picard's Album/Track/File."""
from pathlib import Path
from unittest.mock import MagicMock, patch

from picardtestcase import PicardTestCase

PLUGINS_DIR = Path(__file__).resolve().parent.parent / 'plugins'
PLUGIN_ID = 'highest-disc-total'


class HighestDiscTotalTest(PicardTestCase):
    def setUp(self):
        from picard.extension_points import set_plugin_uuid, unset_plugin_uuid
        from picard.plugin3.plugin import Plugin

        super().setUp()
        self.patch_tagger_instance('picard.item')
        # File.move reaches these; the test tagger doesn't have them.
        self.tagger._acoustid = self.tagger.acoustidmanager = self.tagger.isrc_submit_manager = MagicMock()
        plugin = Plugin(PLUGINS_DIR, PLUGIN_ID)
        plugin.read_manifest()
        self.set_config_values(setting={
            'clear_existing_tags': False,
            'enable_tagger_scripts': False,
            'list_of_scripts': [],
            'preserved_tags': [],
            'plugins3_enabled_plugins': [plugin.manifest.uuid],
        })
        plugin.load_module()
        plugin.enable(self.tagger)
        set_plugin_uuid(plugin.manifest.uuid, PLUGIN_ID)
        self.addCleanup(unset_plugin_uuid, plugin.manifest.uuid)
        self.addCleanup(plugin.disable)
        self.plugin = plugin

    def make_album(self, tracks_per_disc, album_id='album-id'):
        from picard.album import Album
        from picard.track import Track

        album = Album(album_id)
        total = str(len(tracks_per_disc))
        for disc, count in enumerate(tracks_per_disc, 1):
            for n in range(1, count + 1):
                track = Track(f'd{disc}t{n}', album)
                track.metadata['discnumber'] = str(disc)
                track.metadata['totaldiscs'] = total
                track.metadata['tracknumber'] = str(n)
                track.orig_metadata.copy(track.metadata)
                track.scripted_metadata.update(track.metadata)
                album.tracks.append(track)
        return album

    def track(self, album, disc, n=1):
        return next(t for t in album.tracks if t.id == f'd{disc}t{n}')

    def match(self, album, disc, n=1, file_tags=None):
        from picard.file import File

        file = File(f'C:/music/{album.id}-d{disc}t{n}.mp3')
        file.orig_metadata.update(file_tags or {})
        file.metadata.copy(file.orig_metadata)
        track = self.track(album, disc, n)
        file.parent_item = track
        track.add_file(file)
        return file

    def totals(self, album):
        return sorted({f.metadata['totaldiscs'] for t in album.tracks for f in t.files})

    def test_manifest_valid_for_this_picard(self):
        from picard import api_versions_tuple

        manifest = self.plugin.manifest
        assert manifest.validate() == []
        assert set(manifest.api_versions) & set(api_versions_tuple)
        assert manifest.license == 'GPL-3.0-or-later'

    def test_missing_last_disc_lowers_total(self):
        album = self.make_album([16, 12])  # CD + DVD
        for n in range(1, 17):
            self.match(album, 1, n)
        assert self.totals(album) == ['1']

    def test_matching_later_disc_restores_total_and_refreshes_files(self):
        album = self.make_album([3, 3])
        f1 = self.match(album, 1)
        assert self.totals(album) == ['1']
        with patch.object(f1, 'update') as update:
            self.match(album, 2)
        assert self.totals(album) == ['2']
        update.assert_called_once()

    def test_discs_matched_out_of_order(self):
        album = self.make_album([2, 2, 2])
        self.match(album, 3)
        self.match(album, 1)
        self.match(album, 2)
        assert self.totals(album) == ['3']

    def test_removing_last_disc_file_lowers_total_again(self):
        album = self.make_album([3, 3])
        self.match(album, 1)
        f2 = self.match(album, 2)
        self.track(album, 2).remove_file(f2)
        assert self.totals(album) == ['1']

    def test_moving_file_to_an_earlier_disc_lowers_total(self):
        album = self.make_album([3, 3])
        self.match(album, 1)
        f2 = self.match(album, 2)
        f2.move(self.track(album, 1, 2))
        assert self.totals(album) == ['1']

    def test_moving_file_between_albums(self):
        a, b = self.make_album([2, 2], 'a'), self.make_album([3, 3, 3], 'b')
        self.match(a, 1)
        fa2 = self.match(a, 2)
        self.match(b, 1)
        fa2.move(self.track(b, 3))
        assert (self.totals(a), self.totals(b)) == (['1'], ['3'])

    def test_gap_in_middle_uses_highest_disc(self):
        album = self.make_album([2, 2, 2, 2])
        self.match(album, 1)
        self.match(album, 3)
        assert self.totals(album) == ['3']

    def test_missing_last_of_three(self):
        album = self.make_album([2, 2, 2])
        self.match(album, 1)
        self.match(album, 2)
        assert self.totals(album) == ['2']

    def test_each_file_capped_by_its_own_track_total(self):
        album = self.make_album([2, 2])
        for n in (1, 2):
            self.track(album, 2, n).metadata['totaldiscs'] = '1'
        f1, f2 = self.match(album, 1), self.match(album, 2)
        assert (f1.metadata['totaldiscs'], f2.metadata['totaldiscs']) == ('2', '1')

    def test_never_raises_above_musicbrainz_total(self):
        album = self.make_album([2, 2])
        for t in album.tracks:  # MusicBrainz says 1 disc (e.g. edited), files on disc 2
            t.metadata['totaldiscs'] = '1'
        self.match(album, 2)
        assert self.totals(album) == ['1']

    def test_unusable_disc_number_ignored(self):
        for discnumber in ('', '0', 'x'):
            album = self.make_album([2, 2])
            self.track(album, 1).metadata['discnumber'] = discnumber
            with self.assertNoLogs('main', level='ERROR'):
                self.match(album, 1)
            assert self.totals(album) == ['2'], discnumber

    def test_no_musicbrainz_total_left_alone(self):
        album = self.make_album([3, 3])
        for t in album.tracks:
            t.metadata['totaldiscs'] = ''
        with self.assertNoLogs('main', level='ERROR'):
            self.match(album, 1)
        assert self.totals(album) == ['']

    def test_files_already_correct_not_refreshed(self):
        album = self.make_album([3, 3])
        f1 = self.match(album, 1)
        with patch.object(f1, 'update') as update:
            self.match(album, 1, 2)
        update.assert_not_called()

    def test_preserved_totaldiscs_kept(self):
        from picard.file import run_file_pre_save_processors

        self.set_config_values(setting={'preserved_tags': ['totaldiscs']})
        album = self.make_album([3, 3])
        f1 = self.match(album, 1, file_tags={'totaldiscs': '7'})
        run_file_pre_save_processors(f1)
        assert f1.metadata['totaldiscs'] == '7'

    def test_pre_save_fixes_total_reset_without_the_add_hook(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([3, 3])
        f1 = self.match(album, 1)
        # Session restore, or enabling the plugin after files were matched, leaves MusicBrainz's value.
        f1.metadata['totaldiscs'] = '2'
        run_file_pre_save_processors(f1)
        assert f1.metadata['totaldiscs'] == '1'

    def test_pre_save_ignores_unmatched_file(self):
        from picard.file import File, run_file_pre_save_processors

        f = File('C:/music/loose.mp3')
        f.metadata['totaldiscs'] = '2'
        with self.assertNoLogs('main', level='ERROR'):
            run_file_pre_save_processors(f)
        assert f.metadata['totaldiscs'] == '2'

    def test_pre_save_ignores_file_removed_from_its_track(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([3, 3])
        self.match(album, 1)
        f2 = self.match(album, 2)
        self.track(album, 2).remove_file(f2)  # parent_item still points at the track until the file is moved
        f2.metadata['totaldiscs'] = '2'
        with self.assertNoLogs('main', level='ERROR'):
            run_file_pre_save_processors(f2)
        assert f2.metadata['totaldiscs'] == '2'

    def test_pre_save_ignores_album_unmatched_files(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([3, 3, 3])
        self.match(album, 1)
        f = self.match(album, 2, file_tags={'totaldiscs': '2'})
        f.move(album.unmatched_files)
        album.unmatched_files.metadata['totaldiscs'] = '2'  # set by editing Total Discs with the cluster selected
        f.metadata['totaldiscs'] = '2'
        with self.assertNoLogs('main', level='ERROR'):
            run_file_pre_save_processors(f)
        assert f.metadata['totaldiscs'] == '2'

    def test_standalone_recordings_not_scanned(self):
        from picard.album import NatAlbum
        from picard.file import File, run_file_pre_save_processors
        from picard.track import Track

        album = NatAlbum()
        track = Track('rec-id', album)
        album.tracks.append(track)
        file = File('C:/music/rec.mp3')
        file.parent_item = track
        with patch.object(self.plugin._module, 'highest_disc_with_files') as scan:
            track.add_file(file)
            run_file_pre_save_processors(file)
            track.remove_file(file)
        scan.assert_not_called()

    def test_disabled_plugin_leaves_total(self):
        self.set_config_values(setting={'plugins3_enabled_plugins': []})
        album = self.make_album([3, 3])
        self.match(album, 1)
        assert self.totals(album) == ['2']
