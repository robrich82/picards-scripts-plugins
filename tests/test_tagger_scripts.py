"""Run the tagger scripts, in file order, through Picard's script parser on sample track metadata."""
from pathlib import Path

import pytest
from picardtestcase import PicardTestCase

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / 'tagger-scripts'
SCRIPT_FILES = sorted(SCRIPTS_DIR.glob('*.ptsp'))
# Every tag a script may write. Anything else must come out exactly as it went in.
WRITTEN = {'title', 'discsubtitle', 'originaldate', 'date', 'originalyear',
           'albumartist', 'albumartistsort', 'albumartists', '~albumartists'}

BASE = {'album': 'Foo', 'title': 'Song', 'date': '2010-05-01', 'artist': 'Artist', 'tracknumber': '1',
        'musicbrainz_albumartistid': 'id-1', 'albumartist': 'Artist', 'albumartistsort': 'Artist',
        'albumartists': ['Artist'], '_albumartists': ['Artist'], '_albumartists_sort': ['Artist']}


def load_scripts():
    from picard.script.serializer import TaggingScriptInfo

    return [TaggingScriptInfo.create_from_yaml(p.read_text(encoding='utf-8')) for p in SCRIPT_FILES]


def metadata(tags):
    from picard.metadata import Metadata
    from picard.script.parser import normalize_tagname

    m = Metadata()
    for key, value in tags.items():
        m[normalize_tagname(key)] = value
    return m


def run_scripts(tags):
    """Run every script the way Album does when a release loads."""
    from picard.script import ScriptParser

    m = metadata({**BASE, **tags})
    for script in load_scripts():
        ScriptParser().eval(script.script, m)
        m.strip_whitespace()
    return m


def artists(credit, names, sorts=None, sort_credit=None):
    """Album artist credit as Picard builds it from MusicBrainz."""
    return {'albumartist': credit, 'albumartists': names, '_albumartists': names,
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
    ('more precise date in the original year kept', {'originaldate': '1995', 'date': '1995-03-02'},
     {'originaldate': '1995-01-01', 'date': '1995-03-02', 'originalyear': '1995'}),
    ('more precise date in the original month kept', {'originaldate': '1995-03', 'date': '1995-03-02'},
     {'originaldate': '1995-03-01', 'date': '1995-03-02'}),
    ('year-month date in the original year kept and padded', {'originaldate': '1995', 'date': '1995-03'},
     {'date': '1995-03-01'}),
    ('less precise date in the original year replaced', {'originaldate': '1995-03', 'date': '1995'},
     {'date': '1995-03-01'}),
    ('other month in the original year replaced', {'originaldate': '1995-03', 'date': '1995-04-02'},
     {'date': '1995-03-01'}),
    ('date year only padded', {'date': '2010'}, {'date': '2010-01-01', 'originalyear': '2010'}),
    ('date year-month padded', {'date': '2010-05'}, {'date': '2010-05-01', 'originalyear': '2010'}),
    ('date full kept', {'date': '2010-05-01'}, {'date': '2010-05-01', 'originalyear': '2010'}),
    ('no dates', {'date': ''}, {'date': '', 'originaldate': '', 'originalyear': ''}),
    # Various Artists: featured artists stripped, 2+ main artists -> Various Artists
    ('feat. keeps MB sort name',
     artists('The Beatles feat. Billy Preston', ['The Beatles', 'Billy Preston'],
             ['Beatles, The', 'Preston, Billy'], 'Beatles, The feat. Preston, Billy'),
     {'albumartist': 'The Beatles', 'albumartistsort': 'Beatles, The', 'albumartists': ['The Beatles']}),
    ('featuring', artists('Artist A featuring Artist B', ['Artist A', 'Artist B']),
     {'albumartist': 'Artist A', 'albumartistsort': 'Artist A', 'albumartists': ['Artist A']}),
    ('Feat. capitalised', artists('Artist A Feat. Artist B', ['Artist A', 'Artist B']),
     {'albumartist': 'Artist A'}),
    ('ft.', artists('Artist A ft. Artist B', ['Artist A', 'Artist B']), {'albumartist': 'Artist A'}),
    ('ft without a dot', artists('Artist A ft Artist B', ['Artist A', 'Artist B']),
     {'albumartist': 'Artist A', 'albumartists': ['Artist A']}),
    ('feat. without a space', artists('Artist A feat.Artist B', ['Artist A', 'Artist B']),
     {'albumartist': 'Artist A'}),
    ('ft. without a space', artists('Artist A ft.Artist B', ['Artist A', 'Artist B']),
     {'albumartist': 'Artist A'}),
    ('feat without a dot', artists('Artist A feat Artist B', ['Artist A', 'Artist B']),
     {'albumartist': 'Artist A'}),
    ('feat. in parentheses', artists('Artist A (feat. Artist B)', ['Artist A', 'Artist B']),
     {'albumartist': 'Artist A', 'albumartists': ['Artist A']}),
    ('feat. in full-width parentheses', artists('Artist A（feat. Artist B）', ['Artist A', 'Artist B']),
     {'albumartist': 'Artist A'}),
    ('feat with a full-width dot', artists('Artist A feat．Artist B', ['Artist A', 'Artist B']),
     {'albumartist': 'Artist A'}),
    ('meets', artists('Artist A meets Artist B', ['Artist A', 'Artist B']), {'albumartist': 'Artist A'}),
    ('produced by', artists('Artist A produced by Artist B', ['Artist A', 'Artist B']),
     {'albumartist': 'Artist A'}),
    ('two main artists', artists('Artist A & Artist B', ['Artist A', 'Artist B']),
     {'albumartist': 'Various Artists', 'albumartistsort': 'Various Artists',
      'albumartists': ['Artist A', 'Artist B']}),
    ('two main + featured', artists('A & B feat. C', ['A', 'B', 'C']),
     {'albumartist': 'Various Artists', 'albumartists': ['A', 'B']}),
    ('three main artists', artists('A, B & C', ['A', 'B', 'C']),
     {'albumartist': 'Various Artists', 'albumartists': ['A', 'B', 'C']}),
    ('join word inside a later name', artists('X & Little Feat & Y', ['X', 'Little Feat', 'Y']),
     {'albumartist': 'Various Artists', 'albumartists': ['X', 'Little Feat', 'Y']}),
    ('join word inside the last name', artists('A & Sound Meets B', ['A', 'Sound Meets B']),
     {'albumartist': 'Various Artists', 'albumartists': ['A', 'Sound Meets B']}),
    ('feat. after a name containing "Feat"',
     artists('Little Feat feat. Jackson Browne', ['Little Feat', 'Jackson Browne']),
     {'albumartist': 'Little Feat', 'albumartists': ['Little Feat']}),
    ('empty first sort name', artists('A feat. B', ['A', 'B'], ['', 'B, sort']), {'albumartistsort': 'A'}),
    ('one artist keeps a custom sort', artists('The Beatles', ['The Beatles'], ['Beatles, The'], 'Custom Sort'),
     {'albumartist': 'The Beatles', 'albumartistsort': 'Custom Sort'}),
    ('"; " inside a name kept', artists('Foo; Bar', ['Foo; Bar']),
     {'albumartist': 'Foo; Bar', 'albumartists': ['Foo; Bar'], '~albumartists': ['Foo; Bar']}),
    ('one main, two featured', artists('A feat. B & C', ['A', 'B', 'C']),
     {'albumartist': 'A', 'albumartists': ['A']}),
    ('featured name inside main name', artists('Aimer feat. Ai', ['Aimer', 'Ai']),
     {'albumartist': 'Aimer', 'albumartists': ['Aimer']}),
    ('"feat" inside a word', artists('Defeat Squad feat. X', ['Defeat Squad', 'X']),
     {'albumartist': 'Defeat Squad'}),
    ('"ft" starting a word', artists('Band Ftw', ['Band Ftw']), {'albumartist': 'Band Ftw'}),
    ('join word inside one name', artists('East Meets West', ['East Meets West']),
     {'albumartist': 'East Meets West', 'albumartists': ['East Meets West']}),
    ('"feat" word inside one name', artists('Little Feat & Friends', ['Little Feat & Friends']),
     {'albumartist': 'Little Feat & Friends'}),
    ('single artist untouched', artists('The Beatles', ['The Beatles'], ['Beatles, The'], 'Beatles, The'),
     {'albumartist': 'The Beatles', 'albumartistsort': 'Beatles, The', 'albumartists': ['The Beatles']}),
    ('names not matching the credit left alone',
     artists('ArtistA', ['Artist A'], ['A, Artist'], 'Custom Sort'),
     {'albumartist': 'ArtistA', 'albumartistsort': 'Custom Sort', 'albumartists': ['Artist A']}),
    ('parentheses in a name kept', artists('Some Band (Japan)', ['Some Band (Japan)']),
     {'albumartist': 'Some Band (Japan)', 'albumartistsort': 'Some Band (Japan)'}),
    ('name starting with CV kept', artists('CVS Band', ['CVS Band']), {'albumartist': 'CVS Band'}),
    ('name that is only a CV credit not counted', artists('A (CV.X)', ['A', '(CV.X)']),
     {'albumartist': 'X'}),
    # Voice Actor: one credit "Character (CV. Actor)"
    ('CV. one credit',
     artists('春日未来 (CV.山崎はるか)', ['春日未来 (CV.山崎はるか)'], ['Yamazaki, Haruka'], 'Yamazaki, Haruka'),
     {'albumartist': '山崎はるか', 'albumartistsort': 'Yamazaki, Haruka',
      'albumartists': ['山崎はるか'], '~albumartists': ['山崎はるか']}),
    ('CV. one credit, spaced', artists('Char A (CV. Actor X)', ['Char A (CV. Actor X)'], ['X, Actor'], 'X, Actor'),
     {'albumartist': 'Actor X', 'albumartistsort': 'X, Actor', 'albumartists': ['Actor X']}),
    ('CV in sort string falls back to regex', artists('Char A (CV. Actor X)', ['Char A (CV. Actor X)']),
     {'albumartist': 'Actor X', 'albumartistsort': 'Actor X'}),
    ('CV with commas in the sort string',
     artists('Mirai Kasuga (CV. Haruka Yamazaki)', ['Mirai Kasuga (CV. Haruka Yamazaki)'],
             ['Kasuga, Mirai (CV. Yamazaki, Haruka)'], 'Kasuga, Mirai (CV. Yamazaki, Haruka)'),
     {'albumartist': 'Haruka Yamazaki', 'albumartistsort': 'Yamazaki, Haruka'}),
    ('CV with & in the character name', artists('Tom & Jerry (CV. Actor X)', ['Tom & Jerry (CV. Actor X)']),
     {'albumartist': 'Actor X', 'albumartists': ['Actor X']}),
    ('CV with , in the character name', artists('Tom, Jerry (CV. Actor X)', ['Tom, Jerry (CV. Actor X)']),
     {'albumartist': 'Actor X'}),
    ('CV with 、 in the character name', artists('トム、ジェリー (CV.X)', ['トム、ジェリー (CV.X)']),
     {'albumartist': 'X'}),
    ('full-width ＣＶ', artists('春日未来（ＣＶ：山崎はるか）', ['春日未来（ＣＶ：山崎はるか）']),
     {'albumartist': '山崎はるか'}),
    ('full-width dot', artists('Char (CV．Actor X)', ['Char (CV．Actor X)']), {'albumartist': 'Actor X'}),
    ('text after the CV credit kept', artists('Char (CV. Actor X) Remix', ['Char (CV. Actor X) Remix']),
     {'albumartist': 'Char (CV. Actor X) Remix', 'albumartists': ['Char (CV. Actor X) Remix']}),
    ('empty CV. kept', artists('Char (CV.)', ['Char (CV.)']),
     {'albumartist': 'Char (CV.)', 'albumartists': ['Char (CV.)']}),
    ('blank CV: kept', artists('Char (CV: )', ['Char (CV: )']), {'albumartist': 'Char (CV: )'}),
    ('CV. two credits joined with 、',
     artists('春日未来 (CV.山崎はるか)、最上静香 (CV.田所あずさ)',
             ['春日未来 (CV.山崎はるか)', '最上静香 (CV.田所あずさ)'], ['Yamazaki, Haruka', 'Tadokoro, Azusa']),
     {'albumartist': 'Various Artists', 'albumartists': ['山崎はるか', '田所あずさ'],
      '~albumartists': ['山崎はるか', '田所あずさ']}),
    ('CV. credit feat. CV. credit',
     artists('春日未来 (CV.山崎はるか) feat. 最上静香 (CV.田所あずさ)',
             ['春日未来 (CV.山崎はるか)', '最上静香 (CV.田所あずさ)'], ['Yamazaki, Haruka', 'Tadokoro, Azusa']),
     {'albumartist': '山崎はるか', 'albumartistsort': 'Yamazaki, Haruka', 'albumartists': ['山崎はるか']}),
    ('CV. credits joined with "with"',
     artists('Char A (CV. X) with Char B (CV. Y)', ['Char A (CV. X)', 'Char B (CV. Y)'], ['X', 'Y']),
     {'albumartist': 'Various Artists', 'albumartists': ['X', 'Y']}),
    # Voice Actor: character and actor credited as two artists
    ('CV. two artists', artists('Chin-lan Chang(CV.Maki Kawase)', ['Chin-lan Chang', 'Maki Kawase'],
                                ['Chang, Chin-lan', 'Kawase, Maki'], 'Chang, Chin-lan(CV.Kawase, Maki)'),
     {'albumartist': 'Maki Kawase', 'albumartistsort': 'Kawase, Maki', 'albumartists': ['Maki Kawase']}),
    ('CV: two artists', artists('SQ(CV:Akari Kito)', ['SQ', 'Akari Kito'], ['SQ', 'Kito, Akari'],
                                'SQ(CV:Kito, Akari)'),
     {'albumartist': 'Akari Kito', 'albumartistsort': 'Kito, Akari', 'albumartists': ['Akari Kito']}),
    ('CV: one credit', artists('SQ(CV:Akari Kito)', ['SQ(CV:Akari Kito)'], ['Kito, Akari'], 'Kito, Akari'),
     {'albumartist': 'Akari Kito', 'albumartistsort': 'Kito, Akari'}),
    ('CV: spaced', artists('SQ (CV: Akari Kito)', ['SQ', 'Akari Kito'], ['SQ', 'Kito, Akari'],
                           'SQ (CV: Kito, Akari)'),
     {'albumartist': 'Akari Kito', 'albumartistsort': 'Kito, Akari'}),
    ('character with & as two artists', artists('Tom & Jerry(CV.Actor X)', ['Tom & Jerry', 'Actor X'],
                                                ['Tom & Jerry', 'X, Actor'], 'Tom & Jerry(CV.X, Actor)'),
     {'albumartist': 'Actor X', 'albumartistsort': 'X, Actor', 'albumartists': ['Actor X']}),
    ('full-width CV： two artists', artists('春日未来（CV：山崎はるか）', ['春日未来', '山崎はるか'],
                                            ['Kasuga, Mirai', 'Yamazaki, Haruka'], 'Kasuga, Mirai（CV：Yamazaki, Haruka）'),
     {'albumartist': '山崎はるか', 'albumartistsort': 'Yamazaki, Haruka'}),
    ('full-width CV： one credit', artists('春日未来（CV：山崎はるか）', ['春日未来（CV：山崎はるか）'],
                                           ['Yamazaki, Haruka'], 'Yamazaki, Haruka'),
     {'albumartist': '山崎はるか', 'albumartistsort': 'Yamazaki, Haruka'}),
    ('two characters, two actors', artists('A(CV:X) & B(CV:Y)', ['A', 'X', 'B', 'Y']),
     {'albumartist': 'Various Artists', 'albumartistsort': 'Various Artists', 'albumartists': ['X', 'Y']}),
    ('one actor, two characters', artists('A(CV:X) & B(CV:X)', ['A', 'X', 'B', 'X']),
     {'albumartist': 'Various Artists', 'albumartists': ['X']}),
    ('one actor, two of three characters',
     artists('A(CV:X) & B(CV:X) & C(CV:Y)', ['A', 'X', 'B', 'X', 'C', 'Y']),
     {'albumartist': 'Various Artists', 'albumartists': ['X', 'Y']}),
    ('name that is only a full-width ＣＶ credit not counted', artists('A（ＣＶ：X）', ['A', '（ＣＶ：X）']),
     {'albumartist': 'X'}),
    ('one actor, two character credits', artists('A (CV.X) & B (CV.X)', ['A (CV.X)', 'B (CV.X)']),
     {'albumartist': 'Various Artists', 'albumartists': ['X']}),
    ('full-width ＣＶ credits', artists('A（ＣＶ：X）、B（ＣＶ：Y）', ['A（ＣＶ：X）', 'B（ＣＶ：Y）']),
     {'albumartist': 'Various Artists', 'albumartists': ['X', 'Y']}),
    ('character feat. character', artists('A(CV:X) feat. B(CV:Y)', ['A', 'X', 'B', 'Y'],
                                          ['A', 'X-sort', 'B', 'Y-sort'], 'A(CV:X-sort) feat. B(CV:Y-sort)'),
     {'albumartist': 'X', 'albumartistsort': 'X-sort', 'albumartists': ['X']}),
    ('two actors in one CV block', artists('A(CV:X & Y)', ['A', 'X', 'Y']),
     {'albumartist': 'X & Y', 'albumartists': ['X & Y']}),
    ('two actors in one full-width CV block', artists('A（CV：X、Y）', ['A', 'X', 'Y']),
     {'albumartist': 'X、Y'}),
    ('two actors in one CV block feat.',
     artists('A(CV:X & Y) feat. B', ['A', 'X', 'Y', 'B'], ['A-s', 'X-s', 'Y-s', 'B-s'], 'A-s(CV:X-s & Y-s) feat. B-s'),
     {'albumartist': 'X & Y', 'albumartists': ['X & Y'], 'albumartistsort': 'X-s & Y-s'}),
    ('two actors in one full-width CV block (feat.)',
     artists('A（CV：X、Y）（feat. B）', ['A', 'X', 'Y', 'B'], ['A-s', 'X-s', 'Y-s', 'B-s'], 'A-s（CV：X-s、Y-s）（feat. B-s）'),
     {'albumartist': 'X、Y', 'albumartistsort': 'X-s、Y-s'}),
    ('feat word inside an actor sort name',
     artists('A(CV:X & Y) feat. B', ['A', 'X', 'Y', 'B'], ['A-s', 'Kito Meets', 'Y-s', 'B-s'],
             'A-s(CV:Kito Meets & Y-s) feat. B-s'),
     {'albumartist': 'X & Y', 'albumartistsort': 'Kito Meets & Y-s'}),
    ('feat word inside a character name, several actors, feat.',
     artists('East Meets West(CV:X & Y) feat. B', ['East Meets West', 'X', 'Y', 'B'], ['E-s', 'X-s', 'Y-s', 'B-s'],
             'E-s(CV:X-s & Y-s) feat. B-s'),
     {'albumartist': 'X & Y', 'albumartistsort': 'X-s & Y-s'}),
    ('feat inside a CV block, then feat.',
     artists('A(CV:X feat. Y) feat. B', ['A', 'X', 'Y', 'B'], ['A-s', 'X-s', 'Y-s', 'B-s'],
             'A-s(CV:X-s feat. Y-s) feat. B-s'),
     {'albumartist': 'X feat. Y', 'albumartistsort': 'X-s feat. Y-s'}),
    ('CV credit in the join text, feat.',
     artists('A (CV:X) feat. B', ['A', 'B'], ['A-s', 'B-s'], 'A-s (CV:X) feat. B-s'),
     {'albumartist': 'X', 'albumartistsort': 'X'}),
    ('unit credits with several actors',
     artists('A(CV:X、Y) & B(CV:Z)', ['A', 'X', 'Y', 'B', 'Z']),
     {'albumartist': 'Various Artists', 'albumartists': ['X', 'Y', 'Z']}),
    ('full-width unit credits with several actors',
     artists('A（CV：X、Y）、B（CV：Z）', ['A', 'X', 'Y', 'B', 'Z']),
     {'albumartist': 'Various Artists', 'albumartists': ['X', 'Y', 'Z']}),
    ('actor repeated inside a CV block', artists('A(CV:X) & B(CV:Y、X、Z)', ['A', 'X', 'B', 'Y', 'X', 'Z']),
     {'albumartist': 'Various Artists', 'albumartists': ['X', 'Y', 'Z']}),
    ('two CV blocks on one character', artists('A(CV:X)(CV:Y) & B(CV:Z)', ['A', 'X', 'Y', 'B', 'Z']),
     {'albumartist': 'Various Artists', 'albumartists': ['X', 'Y', 'Z']}),
    ('feat inside a CV block', artists('A (CV:X feat. Y)', ['A', 'X', 'Y']),
     {'albumartist': 'X feat. Y', 'albumartists': ['X feat. Y']}),
    ('character, artist, one-credit character',
     artists('A(CV:X) & B & C (CV.Z)', ['A', 'X', 'B', 'C (CV.Z)']),
     {'albumartist': 'Various Artists', 'albumartists': ['X', 'B', 'Z']}),
    *((f'feat. after {sep!r}', artists(f'A{sep}feat. B{end}', ['A', 'B']),
       {'albumartist': 'A', 'albumartists': ['A'], 'albumartistsort': 'A'})
      for sep, end in [(' [', ']'), ('【', '】'), (' ［', '］'), (', ', ''), ('、', ''), ('，', ''), (' - ', ''),
                       (' – ', ''), (' — ', ''), (' / ', ''), ('／', ''), (' & ', ''), (' + ', ''), ('; ', ''),
                       (' ～', '～'), (' ~', '~'), ('「', '」'), ('『', '』'), (' 〈', '〉'), ('《', '》'),
                       (' 〔', '〕'), (' ｛', '｝'), (' {', '}'), (' <', '>'), (': ', ''), (' × ', ''), ('・', ''),
                       (' ＆ ', ''), (' ＋ ', ''), ('；', ''), ('：', ''), (' | ', '')]),
    ('closing bracket before feat. kept', artists('A [JP] feat. B', ['A', 'B']),
     {'albumartist': 'A [JP]', 'albumartists': ['A [JP]']}),
    ('closing 』 before feat. kept', artists('A 『X』 feat. B', ['A', 'B']), {'albumartist': 'A 『X』'}),
    *((f'closing {close!r} before feat. kept',
       artists(f'A {open_}X{close} feat. B', ['A', 'B'], ['A-s', 'B-s'], f'A-s {open_}X{close} feat. B-s'),
       {'albumartist': f'A {open_}X{close}', 'albumartistsort': f'A-s {open_}X{close}'})
      for open_, close in ['""', '《》', '〈〉', '{}', '〔〕', '｢｣', '<>']),
    *((f'{end!r} before feat. kept', artists(f'A{end} feat. B', ['A', 'B']), {'albumartist': f'A{end}'})
      for end in ['!', ' Jr.', '?']),
    ('empty main sort name with join text', artists('A [JP] feat. B', ['A', 'B'], ['', 'B-s']),
     {'albumartist': 'A [JP]', 'albumartistsort': 'A [JP]'}),
    ('"ft" ending a word is no feat', artists('A soft B', ['A', 'B']), {'albumartist': 'Various Artists'}),
    ('"ft" starting a word is no feat', artists('A ftw B', ['A', 'B']), {'albumartist': 'Various Artists'}),
    ('featured name inside a capitalised "Feat"', artists('A Feat. e', ['A', 'e']), {'albumartist': 'A'}),
    ('featured name inside "Featuring"', artists('A Featuring in', ['A', 'in']), {'albumartist': 'A'}),
    ('name starting with punctuation', artists('? & Who?', ['?', 'Who?']),
     {'albumartist': 'Various Artists', 'albumartists': ['?', 'Who?']}),
    ('name starting with punctuation inside a later name', artists('A & ? & Who?', ['A', '?', 'Who?']),
     {'albumartist': 'Various Artists', 'albumartists': ['A', '?', 'Who?']}),
    ('featured name inside "feat"', artists('A feat. e', ['A', 'e']), {'albumartist': 'A', 'albumartists': ['A']}),
    ('featured name inside "featuring"', artists('A featuring in', ['A', 'in']), {'albumartist': 'A'}),
    ('featured name inside "produced by"', artists('A produced by y', ['A', 'y']), {'albumartist': 'A'}),
    ('actor name inside "CV"', artists('A(CV:V)', ['A', 'V']), {'albumartist': 'V', 'albumartists': ['V']}),
    ('actor name inside "CV."', artists('A (CV.C)', ['A', 'C']), {'albumartist': 'C'}),
    ('actor name inside full-width "ＣＶ"', artists('A（ＣＶ：Ｖ）', ['A', 'Ｖ']), {'albumartist': 'Ｖ'}),
    ('actor name inside "CV．"', artists('A(CV．V)', ['A', 'V']), {'albumartist': 'V'}),
    ('second artist named like a join word', artists('A & Feat', ['A', 'Feat']),
     {'albumartist': 'Various Artists', 'albumartists': ['A', 'Feat']}),
    ('featured artist named like a join word', artists('A feat. Feat', ['A', 'Feat']),
     {'albumartist': 'A', 'albumartists': ['A']}),
    ('second artist starting with a join word', artists('A & Meets Band', ['A', 'Meets Band']),
     {'albumartist': 'Various Artists', 'albumartists': ['A', 'Meets Band']}),
]


@pytest.mark.parametrize(('tags', 'expected'), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_scripts(tags, expected):
    result = run_scripts(tags)
    for key, value in expected.items():
        if isinstance(value, list):
            assert result.getall(key) == value, key
        else:
            assert result[key] == value, key


@pytest.mark.parametrize('tags', [c[1] for c in CASES], ids=[c[0] for c in CASES])
def test_scripts_change_nothing_else(tags):
    before = metadata({**BASE, **tags})
    result = run_scripts(tags)
    assert set(result) - set(before) <= WRITTEN, 'new tags or leftover temporary variables'
    unchanged = set(before) - WRITTEN
    assert {k: result.getall(k) for k in unchanged} == {k: before.getall(k) for k in unchanged}


class TestTrackFiles(PicardTestCase):
    """Run the scripts the way Picard does when files are matched to a track."""

    def setUp(self):
        super().setUp()
        self.patch_tagger_instance('picard.item')
        self.set_config_values(setting={
            'enable_tagger_scripts': True,
            'list_of_scripts': [(i, s.title, True, s.script) for i, s in enumerate(load_scripts())],
            'clear_existing_tags': True,
            'preserved_tags': [],
        })

    def make_track(self, tags, base=BASE):
        from picard.script import ScriptParser, iter_active_tagging_scripts
        from picard.track import Track

        track = Track('track-id')
        track.metadata.update(metadata({**base, **tags}))
        track.orig_metadata.copy(track.metadata)
        for script in iter_active_tagging_scripts():
            ScriptParser().eval(script.content, track.metadata)
            track.metadata.strip_whitespace()
            track.scripted_metadata.update(track.metadata)
        return track

    def match(self, track, name, file_tags):
        from picard.file import File

        file = File(f'C:/music/{name}.mp3')
        file.orig_metadata.update(metadata(file_tags))
        file.metadata.copy(file.orig_metadata)
        file.parent_item = track
        track.add_file(file)
        return file.metadata

    def test_clear_existing_tags_drops_old_file_values(self):
        track = self.make_track({'date': ''})
        result = self.match(track, 'a', {'originaldate': '1990', 'date': '1991', 'discsubtitle': 'old disc'})
        assert (result['originaldate'], result['date'], result['discsubtitle'], result['originalyear']) == (
            '', '', '', '')

    def test_kept_file_values_still_padded_without_clear_existing_tags(self):
        self.set_config_values(setting={'clear_existing_tags': False})
        track = self.make_track({'date': ''})
        result = self.match(track, 'a', {'originaldate': '1990', 'discsubtitle': 'old disc'})
        assert (result['originaldate'], result['date'], result['discsubtitle']) == (
            '1990-01-01', '1990-01-01', 'Old Disc')

    def test_standalone_recording_drops_old_album_artist_tags(self):
        track = self.make_track({'title': 'Song', 'artist': 'Artist'}, base={})
        result = self.match(track, 'a', {'albumartist': 'Old Char (CV.Old Actor)', 'albumartistsort': 'Old sort',
                                         'albumartists': ['Old A', 'Old B']})
        assert [k for k in ('albumartist', 'albumartistsort', 'albumartists') if k in result] == []

    def test_various_artists_ignores_old_file_values(self):
        track = self.make_track(artists('Artist A & Artist B', ['Artist A', 'Artist B']))
        result = self.match(track, 'a', {'albumartists': ['Old'], 'albumartistsort': 'Old sort'})
        assert (result['albumartist'], result['albumartistsort'], result.getall('albumartists')) == (
            'Various Artists', 'Various Artists', ['Artist A', 'Artist B'])

    def test_hidden_variables_in_file_tags_ignored(self):
        junk = {f'~{name}': 'Junk' for name in ('feat', 'aa', 'aas', 'name', 'va', 'incv')}
        for credit, names, expected in [('A(CV:X) & B(CV:Y)', ['A', 'X', 'B', 'Y'], ('Various Artists', ['X', 'Y'], 'Various Artists')),
                                        ('A feat. B', ['A', 'B'], ('A', ['A'], 'A'))]:
            result = self.match(self.make_track(artists(credit, names)), 'a', junk)
            assert (result['albumartist'], result.getall('albumartists'), result['albumartistsort']) == expected, credit

    def test_every_matched_file_gets_the_same_result(self):
        track = self.make_track({**artists('SQ(CV:Akari Kito & Y)', ['SQ(CV:Akari Kito & Y)']),
                                 'originaldate': '1995'})
        files = [self.match(track, name, {'albumartist': 'Old', 'albumartists': ['Old']}) for name in 'ab']
        for result in [track.metadata, *files]:
            assert (result['albumartist'], result.getall('albumartists'), result['date']) == (
                'Akari Kito & Y', ['Akari Kito & Y'], '1995-01-01')
