"""Set Total Discs to the highest disc number that has matched files."""

from weakref import WeakKeyDictionary

from picard.album import NatAlbum
from picard.plugin3.api import Track
from picard.tags.preserved import UserPreservedTags  # what File.copy_metadata checks

# file -> {tag: value the plugin last wrote}. Any value other than this or MusicBrainz's
# was set by the user or the session, and is left alone.
_written = WeakKeyDictionary()


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _album(track):
    """The track's album, or None for standalone recordings: they have no discs, and the
    album can hold thousands of tracks that would be rescanned on every file event."""
    album = track.album
    return None if isinstance(album, NatAlbum) else album


def highest_disc_with_files(album):
    discs = [_int(t.metadata['discnumber']) for t in album.tracks if t.files]
    return max((d for d in discs if d), default=None)


def _set_tag(file, tag, mb_value, target):
    current = file.metadata[tag]
    if current == target or tag in UserPreservedTags():
        return False
    if current != mb_value and current != _written.get(file, {}).get(tag):
        return False
    file.metadata[tag] = target
    _written.setdefault(file, {})[tag] = target
    return True


def apply_to_file(track, file, highest):
    # Cap at the track's own totaldiscs (MusicBrainz's value, after scripts) so the total
    # can go back up when files for a later disc are matched.
    mb_total = track.metadata['totaldiscs']
    if not _int(mb_total) or not highest:
        return False
    return _set_tag(file, 'totaldiscs', mb_total, str(min(_int(mb_total), highest)))


def apply_to_album(album):
    if album is None:
        return
    highest = highest_disc_with_files(album)
    for track in album.tracks:
        for file in track.files:
            if apply_to_file(track, file, highest):
                file.update()


def on_file_added(api, track, file):
    apply_to_album(_album(track))


def on_file_removed(api, track, file):
    apply_to_album(_album(track))


def on_file_pre_save(api, file):
    track = file.parent_item
    # parent_item can be a Cluster (an album's unmatched files), and still points at the
    # track after remove_file, so require a Track that holds the file.
    if not isinstance(track, Track) or file not in track.files:
        return
    album = _album(track)
    if album is not None:
        apply_to_file(track, file, highest_disc_with_files(album))


def enable(api):
    api.register_file_post_addition_to_track_processor(on_file_added)
    api.register_file_post_removal_from_track_processor(on_file_removed)
    api.register_file_pre_save_processor(on_file_pre_save)
