"""Set Total Discs to the highest disc number that has matched files."""

from picard.album import NatAlbum
from picard.plugin3.api import Track
from picard.tags.preserved import UserPreservedTags  # what File.copy_metadata checks

TAGS = ('totaldiscs',)


def _marker(tag):
    # The value the plugin last wrote to tag. Picard neither saves nor compares ~ variables,
    # and drops them when it rebuilds the file's metadata on matching.
    return f'~highest_disc_total_{tag}'


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


def _baseline(track, tag):
    """MusicBrainz's value, after tagger scripts. Not track.metadata: editing a track row in
    the metadata box writes there too. scripted_metadata is only filled when scripts ran."""
    return track.scripted_metadata[tag] or track.orig_metadata[tag]


def highest_disc_with_files(album):
    discs = [_int(_baseline(t, 'discnumber')) for t in album.tracks if t.files]
    return max((d for d in discs if d), default=None)


def _set_tag(file, tag, baseline, target):
    # The tag is the plugin's to change while it holds the value the plugin last wrote or,
    # before that, the baseline. Anything else is a user edit, even the baseline typed back.
    current = file.metadata[tag]
    if tag in UserPreservedTags() or current != (file.metadata[_marker(tag)] or baseline):
        return False
    if current == target:
        return False
    file.metadata[tag] = file.metadata[_marker(tag)] = target
    return True


def apply_to_file(track, file, highest):
    # Cap at the track's own total so it can go back up when files for a later disc are matched.
    mb_total = _baseline(track, 'totaldiscs')
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
    # Picard has just rebuilt the file's tags from the track, so any marker is stale.
    for tag in TAGS:
        file.metadata.unset(_marker(tag))
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
