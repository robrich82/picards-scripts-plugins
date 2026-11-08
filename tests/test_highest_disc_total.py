"""The Highest Disc Total plugin, loaded and enabled the way Picard does, against Picard's Album/Track/File."""
from pathlib import Path

from picardtestcase import PicardTestCase

PLUGINS_DIR = Path(__file__).resolve().parent.parent / 'plugins'
PLUGIN_ID = 'highest-disc-total'


class HighestDiscTotalTest(PicardTestCase):
    def setUp(self):
        from picard.extension_points import set_plugin_uuid, unset_plugin_uuid
        from picard.plugin3.plugin import Plugin

        super().setUp()
        self.patch_tagger_instance('picard.item')
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

    def make_album(self, tracks_per_disc):
        from picard.album import Album
        from picard.track import Track

        album = Album('album-id')
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

    def match(self, album, disc, n=1):
        from picard.file import File

        file = File(f'C:/music/d{disc}t{n}.mp3')
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

    def test_matching_later_disc_restores_total(self):
        album = self.make_album([3, 3])
        self.match(album, 1)
        assert self.totals(album) == ['1']
        self.match(album, 2)
        assert self.totals(album) == ['2']

    def test_removing_last_disc_file_lowers_total_again(self):
        album = self.make_album([3, 3])
        self.match(album, 1)
        f2 = self.match(album, 2)
        self.track(album, 2).remove_file(f2)
        assert self.totals(album) == ['1']

    def test_gap_in_middle_uses_highest_disc(self):
        album = self.make_album([2, 2, 2])
        self.match(album, 1)
        self.match(album, 3)
        assert self.totals(album) == ['3']

    def test_missing_last_of_three(self):
        album = self.make_album([2, 2, 2])
        self.match(album, 1)
        self.match(album, 2)
        assert self.totals(album) == ['2']

    def test_single_disc_unchanged(self):
        album = self.make_album([5])
        self.match(album, 1)
        assert self.totals(album) == ['1']

    def test_pre_save_fixes_total_reset_by_track_refresh(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([3, 3])
        f1 = self.match(album, 1)
        # Re-copying track metadata (e.g. after editing the track) resets totaldiscs to MusicBrainz's value.
        f1.metadata['totaldiscs'] = '2'
        run_file_pre_save_processors(f1)
        assert f1.metadata['totaldiscs'] == '1'

    def test_pre_save_ignores_unmatched_file(self):
        from picard.file import File, run_file_pre_save_processors

        f = File('C:/music/loose.mp3')
        f.metadata['totaldiscs'] = '2'
        run_file_pre_save_processors(f)
        assert f.metadata['totaldiscs'] == '2'

    def test_never_raises_above_musicbrainz_total(self):
        album = self.make_album([2, 2])
        for t in album.tracks:  # MusicBrainz says 1 disc (e.g. edited), files on disc 2
            t.metadata['totaldiscs'] = '1'
        self.match(album, 2)
        assert self.totals(album) == ['1']

    def test_pre_save_ignores_file_removed_from_its_track(self):
        from picard.file import run_file_pre_save_processors

        album = self.make_album([3, 3])
        self.match(album, 1)
        f2 = self.match(album, 2)
        self.track(album, 2).remove_file(f2)  # parent_item still points at the track until the file is moved
        f2.metadata['totaldiscs'] = '2'
        run_file_pre_save_processors(f2)
        assert f2.metadata['totaldiscs'] == '2'

    def test_no_musicbrainz_total_left_alone(self):
        album = self.make_album([3, 3])
        for t in album.tracks:
            t.metadata['totaldiscs'] = ''
        with self.assertNoLogs('main', level='ERROR'):
            self.match(album, 1)
        assert self.totals(album) == ['']

    def test_files_already_correct_not_refreshed(self):
        from unittest.mock import patch

        album = self.make_album([3, 3])
        f1 = self.match(album, 1)
        with patch.object(f1, 'update') as update:
            self.match(album, 1, 2)
        update.assert_not_called()

    def test_disabled_plugin_leaves_total(self):
        self.set_config_values(setting={'plugins3_enabled_plugins': []})
        album = self.make_album([3, 3])
        self.match(album, 1)
        assert self.totals(album) == ['2']
