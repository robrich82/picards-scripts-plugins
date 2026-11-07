"""Make Picard's source importable and give tests a config that falls back to Picard's defaults.

The defaulting config is adapted from Picard's own test/conftest.py (GPL-2.0-or-later).
"""
from contextlib import suppress
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

PICARD_SRC = Path(os.environ.get('PICARD_SRC', Path(__file__).resolve().parent.parent / '.picard' / 'src'))
if not (PICARD_SRC / 'picard').is_dir():
    pytest.exit(f'Picard source not found at {PICARD_SRC}; run: python tools/run_tests.py', returncode=2)
# picard-src/test is added so picardtestcase imports as a plain module, without pulling in
# Picard's test package (its __init__ imports every Picard test module).
sys.path[:0] = [str(PICARD_SRC), str(PICARD_SRC / 'test')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def pytest_report_header():
    from picard import PICARD_VERSION_STR

    return f'picard: {PICARD_VERSION_STR} from {PICARD_SRC}'


def _defaulting_section(section_name):
    class _Section(dict):
        def __getitem__(self, name):
            if dict.__contains__(self, name):
                return dict.__getitem__(self, name)
            from picard.config import Option

            opt = Option.get(section_name, name)
            if opt is not None:
                return opt.default
            raise KeyError(name)

        def raw_value(self, name, qtype=None):
            return dict.get(self, name)

        def key(self, name):
            return f'{section_name}/{name}'

        def remove(self, name):
            with suppress(KeyError):
                del self[name]

    return _Section()


@pytest.fixture(autouse=True)
def picard_config(monkeypatch):
    import picard.config as cfg
    import picard.options  # noqa: F401  registers every Option and its default
    import picardtestcase

    config = SimpleNamespace(
        setting=_defaulting_section('setting'),
        persist=_defaulting_section('persist'),
        profiles=_defaulting_section('profiles'),
    )

    def install():
        cfg.config = config
        cfg.setting, cfg.persist, cfg.profiles = config.setting, config.persist, config.profiles

    install()
    monkeypatch.setattr(cfg, 'get_config', lambda: config, raising=False)
    monkeypatch.setattr(picardtestcase.PicardTestCase, 'init_config', staticmethod(install), raising=False)
    return config
