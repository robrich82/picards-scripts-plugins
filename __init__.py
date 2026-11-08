"""Set Total Discs to the highest disc number that has matched files."""


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def highest_disc_with_files(album):
    discs = [_int(t.metadata['discnumber']) for t in album.tracks if t.files]
    discs = [d for d in discs if d]
    return max(discs) if discs else None


def apply_to_file(track, file, highest):
    # Start from the track's MusicBrainz value so the total can go back up
    # when files for a later disc are matched.
    mb_total = _int(track.metadata['totaldiscs'])
    if not mb_total or not highest:
        return False
    target = str(min(mb_total, highest))
    if file.metadata['totaldiscs'] == target:
        return False
    file.metadata['totaldiscs'] = target
    return True


def apply_to_album(album):
    if album is None:
        return
    highest = highest_disc_with_files(album)
    for track in album.tracks:
        for file in track.files:
            if apply_to_file(track, file, highest):
                file.update()


def on_file_added(api, track, file):
    apply_to_album(getattr(track, 'album', None))


def on_file_removed(api, track, file):
    apply_to_album(getattr(track, 'album', None))


def on_file_pre_save(api, file):
    track = file.parent_item
    album = getattr(track, 'album', None)
    if album is None or file not in getattr(track, 'files', ()):
        return
    apply_to_file(track, file, highest_disc_with_files(album))


def enable(api):
    api.register_file_post_addition_to_track_processor(on_file_added)
    api.register_file_post_removal_from_track_processor(on_file_removed)
    api.register_file_pre_save_processor(on_file_pre_save)
