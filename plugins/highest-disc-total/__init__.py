"""Set Total Discs to the number of discs with matched files, and renumber later discs down to
close the gaps."""

from picard.album import NatAlbum
from picard.plugin3.api import Track
from picard.tags.preserved import UserPreservedTags  # what File.copy_metadata checks


def _marker(tag):
    # The value the plugin last wrote to tag. Picard neither writes ~ variables to tags nor
    # compares them, but does carry them through re-matching and saving; a stale one is
    # harmless because the baseline still counts as the plugin's.
    return f'~highest_disc_total_{tag}'


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(n, like):
    """n, zero-padded to the width of like when like is: a script's 01 stays 01."""
    return str(n).zfill(len(like)) if like.startswith('0') else str(n)


def _album(track):
    """The track's album, or None for standalone recordings: they have no discs, and the
    album can hold thousands of tracks that would be rescanned on every file event."""
    album = track.album
    return None if isinstance(album, NatAlbum) else album


def _baseline(track, tag):
    """MusicBrainz's value, after tagger scripts. Not track.metadata: editing a track row in
    the metadata box writes there too. scripted_metadata is only filled when scripts ran."""
    return track.scripted_metadata[tag] or track.orig_metadata[tag]


def discs_with_files(album):
    """Sorted, distinct disc numbers of the tracks that have matched files."""
    discs = {_int(_baseline(t, 'discnumber')) for t in album.tracks if t.files}
    return sorted(d for d in discs if d is not None and d > 0)


def _set_tag(file, tag, baseline, target):
    # The tag is the plugin's to change while it holds the baseline or the value the plugin
    # last wrote; anything else is a user edit. Preserved Tags pins even the baseline.
    current = file.metadata[tag]
    if tag in UserPreservedTags() or current not in {baseline, file.metadata[_marker(tag)]} - {''}:
        return False
    if current == target:
        return False
    file.metadata[tag] = file.metadata[_marker(tag)] = target
    return True


def apply_to_file(track, file, discs):
    if not discs:
        return False
    changed = False
    mb_disc = _baseline(track, 'discnumber')
    if _int(mb_disc) in discs:
        new_disc = discs.index(_int(mb_disc)) + 1
        changed = _set_tag(file, 'discnumber', mb_disc, _number(new_disc, mb_disc))
    mb_total = _baseline(track, 'totaldiscs')
    if _int(mb_total):
        # Capped at the track's own total, so it goes back up when a missing disc is matched,
        # and never below a disc number left in place (preserved or edited).
        kept_disc = _int(file.metadata['discnumber']) or 0
        total = min(_int(mb_total), max(len(discs), kept_disc))
        changed = _set_tag(file, 'totaldiscs', mb_total, _number(total, mb_total)) or changed
    return changed


def apply_to_album(album):
    if album is None:
        return
    discs = discs_with_files(album)
    for track in album.tracks:
        for file in track.files:
            if apply_to_file(track, file, discs):
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
        apply_to_file(track, file, discs_with_files(album))


def enable(api):
    api.register_file_post_addition_to_track_processor(on_file_added)
    api.register_file_post_removal_from_track_processor(on_file_removed)
    api.register_file_pre_save_processor(on_file_pre_save)
