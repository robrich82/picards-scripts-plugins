"""Run the tagger scripts, in file order, through Picard's script parser on sample track metadata."""
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / 'tagger-scripts'
SCRIPT_FILES = sorted(SCRIPTS_DIR.glob('*.ptsp'))
HIDDEN_TEMPS = ('~aa', '~cvx', '~rest', '~main', '~name', '~cvre', '~clean', '~d', '~loop_value', '~loop_count')

BASE = {'album': 'Foo', 'title': 'Song', 'date': '2010-05-01',
        'albumartist': 'Artist', 'albumartistsort': 'Artist',
        '_albumartists': ['Artist'], '_albumartists_sort': ['Artist']}


def load_scripts():
    from picard.script.serializer import TaggingScriptInfo

    return [TaggingScriptInfo.create_from_yaml(p.read_text(encoding='utf-8')) for p in SCRIPT_FILES]


def run_scripts(tags):
    from picard.metadata import Metadata
    from picard.script import ScriptParser

    m = Metadata()
    for key, value in {**BASE, **tags}.items():
        m['~' + key[1:] if key.startswith('_') else key] = value
    for script in load_scripts():
        ScriptParser().eval(script.script, m)
    return m


def artists(credit, names, sorts=None, sort_credit=None):
    """Album artist credit as Picard builds it from MusicBrainz."""
    return {'albumartist': credit, '_albumartists': names,
            'albumartistsort': sort_credit or credit, '_albumartists_sort': sorts or names}


def test_scripts_import_with_titles_in_run_order():
    assert [s.title for s in load_scripts()] == [
        'Remove Hyphens in Title', 'Set Subtitle', 'Use Original Date', 'Various Artists', 'Voice Actor']


CASES = [
    # Remove Hyphens in Title
    ('spaced hyphen -> en dash', {'title': 'Song - Live'}, {'title': 'Song – Live'}),
    ('unspaced hyphen kept', {'title': 'Re-Mix'}, {'title': 'Re-Mix'}),
    # Set Subtitle
    ('disc subtitle title-cased', {'discsubtitle': 'the first disc'},
     {'discsubtitle': 'The First Disc', 'set subtitle': ''}),
    ('no disc subtitle', {}, {'discsubtitle': ''}),
    # Use Original Date
    ('original year only padded', {'originaldate': '1995', 'date': '2010-05-01'},
     {'originaldate': '1995-01-01', 'date': '1995-01-01', 'originalyear': '1995'}),
    ('original year-month padded', {'originaldate': '1995-03', 'date': '2010-05-01'},
     {'originaldate': '1995-03-01', 'date': '1995-03-01', 'originalyear': '1995'}),
    ('original full date used', {'originaldate': '1995-03-02', 'date': '2010'},
     {'originaldate': '1995-03-02', 'date': '1995-03-02', 'originalyear': '1995'}),
    ('date year only padded', {'date': '2010'}, {'date': '2010-01-01', 'originalyear': '2010'}),
    ('date year-month padded', {'date': '2010-05'}, {'date': '2010-05-01', 'originalyear': '2010'}),
    ('date full kept', {'date': '2010-05-01'}, {'date': '2010-05-01', 'originalyear': '2010'}),
    ('no dates', {'date': ''}, {'date': '', 'originaldate': '', 'originalyear': ''}),
    # Various Artists: featured artists stripped, 2+ main artists -> Various Artists
    ('feat. keeps MB sort name',
     artists('The Beatles feat. Billy Preston', ['The Beatles', 'Billy Preston'],
             ['Beatles, The', 'Preston, Billy'], 'Beatles, The feat. Preston, Billy'),
     {'albumartist': 'The Beatles', 'albumartistsort': 'Beatles, The'}),
    ('featuring', artists('Artist A featuring Artist B', ['Artist A', 'Artist B']),
     {'albumartist': 'Artist A', 'albumartistsort': 'Artist A'}),
    ('Feat. capitalised', artists('Artist A Feat. Artist B', ['Artist A', 'Artist B']),
     {'albumartist': 'Artist A'}),
    ('ft.', artists('Artist A ft. Artist B', ['Artist A', 'Artist B']), {'albumartist': 'Artist A'}),
    ('two main artists', artists('Artist A & Artist B', ['Artist A', 'Artist B']),
     {'albumartist': 'Various Artists', 'albumartistsort': 'Various Artists'}),
    ('two main + featured', artists('A & B feat. C', ['A', 'B', 'C']), {'albumartist': 'Various Artists'}),
    ('one main, two featured', artists('A feat. B & C', ['A', 'B', 'C']), {'albumartist': 'A'}),
    ('featured name inside main name', artists('Aimer feat. Ai', ['Aimer', 'Ai']), {'albumartist': 'Aimer'}),
    ('"feat" inside a word', artists('Defeat Squad feat. X', ['Defeat Squad', 'X']),
     {'albumartist': 'Defeat Squad'}),
    ('single artist untouched', artists('The Beatles', ['The Beatles'], ['Beatles, The'], 'Beatles, The'),
     {'albumartist': 'The Beatles', 'albumartistsort': 'Beatles, The'}),
    ('parentheses in a name kept', artists('Some Band (Japan)', ['Some Band (Japan)']),
     {'albumartist': 'Some Band (Japan)', 'albumartistsort': 'Some Band (Japan)'}),
    ('name starting with CV kept', artists('CVS Band', ['CVS Band']), {'albumartist': 'CVS Band'}),
    # Voice Actor: one credit "Character (CV. Actor)"
    ('CV. one credit',
     artists('春日未来 (CV.山崎はるか)', ['春日未来 (CV.山崎はるか)'], ['Yamazaki, Haruka'], 'Yamazaki, Haruka'),
     {'albumartist': '山崎はるか', 'albumartistsort': 'Yamazaki, Haruka', '~albumartists': ['山崎はるか']}),
    ('CV. one credit, spaced', artists('Char A (CV. Actor X)', ['Char A (CV. Actor X)'], ['X, Actor'], 'X, Actor'),
     {'albumartist': 'Actor X', 'albumartistsort': 'X, Actor'}),
    ('CV in sort string falls back to regex', artists('Char A (CV. Actor X)', ['Char A (CV. Actor X)']),
     {'albumartist': 'Actor X', 'albumartistsort': 'Actor X'}),
    ('CV. two credits joined with 、',
     artists('春日未来 (CV.山崎はるか)、最上静香 (CV.田所あずさ)',
             ['春日未来 (CV.山崎はるか)', '最上静香 (CV.田所あずさ)'], ['Yamazaki, Haruka', 'Tadokoro, Azusa']),
     {'albumartist': 'Various Artists', '~albumartists': ['山崎はるか', '田所あずさ']}),
    ('CV. credit feat. CV. credit',
     artists('春日未来 (CV.山崎はるか) feat. 最上静香 (CV.田所あずさ)',
             ['春日未来 (CV.山崎はるか)', '最上静香 (CV.田所あずさ)'], ['Yamazaki, Haruka', 'Tadokoro, Azusa']),
     {'albumartist': '山崎はるか', 'albumartistsort': 'Yamazaki, Haruka'}),
    ('CV. credits joined with "with"',
     artists('Char A (CV. X) with Char B (CV. Y)', ['Char A (CV. X)', 'Char B (CV. Y)'], ['X', 'Y']),
     {'albumartist': 'Various Artists'}),
    # Voice Actor: character and actor credited as two artists
    ('CV. two artists', artists('Chin-lan Chang(CV.Maki Kawase)', ['Chin-lan Chang', 'Maki Kawase'],
                                ['Chang, Chin-lan', 'Kawase, Maki'], 'Chang, Chin-lan(CV.Kawase, Maki)'),
     {'albumartist': 'Maki Kawase', 'albumartistsort': 'Kawase, Maki'}),
    ('CV: two artists', artists('SQ(CV:Akari Kito)', ['SQ', 'Akari Kito'], ['SQ', 'Kito, Akari'],
                                'SQ(CV:Kito, Akari)'),
     {'albumartist': 'Akari Kito', 'albumartistsort': 'Kito, Akari'}),
    ('CV: one credit', artists('SQ(CV:Akari Kito)', ['SQ(CV:Akari Kito)'], ['Kito, Akari'], 'Kito, Akari'),
     {'albumartist': 'Akari Kito', 'albumartistsort': 'Kito, Akari'}),
    ('CV: spaced', artists('SQ (CV: Akari Kito)', ['SQ', 'Akari Kito'], ['SQ', 'Kito, Akari'],
                           'SQ (CV: Kito, Akari)'),
     {'albumartist': 'Akari Kito', 'albumartistsort': 'Kito, Akari'}),
    ('full-width CV： two artists', artists('春日未来（CV：山崎はるか）', ['春日未来', '山崎はるか'],
                                            ['Kasuga, Mirai', 'Yamazaki, Haruka'], 'Kasuga, Mirai（CV：Yamazaki, Haruka）'),
     {'albumartist': '山崎はるか', 'albumartistsort': 'Yamazaki, Haruka'}),
    ('full-width CV： one credit', artists('春日未来（CV：山崎はるか）', ['春日未来（CV：山崎はるか）'],
                                           ['Yamazaki, Haruka'], 'Yamazaki, Haruka'),
     {'albumartist': '山崎はるか', 'albumartistsort': 'Yamazaki, Haruka'}),
    ('two characters, two actors', artists('A(CV:X) & B(CV:Y)', ['A', 'X', 'B', 'Y']),
     {'albumartist': 'Various Artists', 'albumartistsort': 'Various Artists'}),
    ('character feat. character', artists('A(CV:X) feat. B(CV:Y)', ['A', 'X', 'B', 'Y'],
                                          ['A', 'X-sort', 'B', 'Y-sort'], 'A(CV:X-sort) feat. B(CV:Y-sort)'),
     {'albumartist': 'X', 'albumartistsort': 'X-sort'}),
]


@pytest.mark.parametrize(('tags', 'expected'), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_scripts(tags, expected):
    result = run_scripts(tags)
    for key, value in expected.items():
        assert result.getall(key) == value if isinstance(value, list) else result[key] == value, key


def test_scripts_leave_no_temporary_variables():
    result = run_scripts({**artists('SQ(CV:Akari Kito) feat. B', ['SQ', 'Akari Kito', 'B']),
                          'originaldate': '1995', 'title': 'Song - Live'})
    assert [t for t in HIDDEN_TEMPS if t in result] == []
