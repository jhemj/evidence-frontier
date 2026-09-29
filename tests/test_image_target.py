import sys
from types import SimpleNamespace
import pytest
from workbench.image_target import open_target
from workbench.worker import segments


def segment(path, number=1):
    path.write_bytes(b'EVF\x09\x0d\x0a\xff\x00' + b'\x01' + number.to_bytes(2, 'little') + b'\x00\x00')
    return path


def test_exact_segments_ignore_sidecars_and_literal_glob_names(tmp_path):
    first = segment(tmp_path / 'Synthetic [7].e01')
    second = segment(tmp_path / 'Synthetic [7].e02', 2)
    (tmp_path / 'Synthetic [7].txt').write_text('acquisition notes')
    segment(tmp_path / 'Synthetic 7.e01')
    assert segments(first) == [first, second]


def test_ewf_uses_handles_and_closes_on_parser_failure(tmp_path, monkeypatch):
    first = segment(tmp_path / 'Synthetic 7.e01')
    second = segment(tmp_path / 'Synthetic 7.e02', 2)
    (tmp_path / 'Synthetic 7.txt').write_text('not a segment')
    handles = []
    class Container:
        def __init__(self, values):
            handles.extend(values)
            assert [h.name for h in values] == [str(first), str(second)]
            assert all(h.mode == 'rb' for h in values)
        def close(self): pass
    class Target:
        def __init__(self, path): self.disks = SimpleNamespace(add=lambda c: None)
        def apply(self): raise ValueError('parser failed')
    monkeypatch.setitem(sys.modules, 'dissect.target', SimpleNamespace(Target=Target))
    monkeypatch.setitem(sys.modules, 'dissect.target.containers.ewf', SimpleNamespace(EwfContainer=Container))
    with pytest.raises(ValueError, match='parser failed'):
        with open_target(first): pass
    assert all(h.closed for h in handles)


@pytest.mark.parametrize('variant', ['gap', 'duplicate', 'signature', 'number', 'symlink'])
def test_invalid_segments_fail_closed(tmp_path, variant):
    first = segment(tmp_path / 'disk.e01')
    if variant == 'gap': segment(tmp_path / 'disk.e03', 3)
    elif variant == 'duplicate': segment(tmp_path / 'disk.E01')
    elif variant == 'signature': first.write_bytes(b'invalid header')
    elif variant == 'number': segment(first, 2)
    else: (tmp_path / 'disk.e02').symlink_to(first)
    with pytest.raises(ValueError):
        with open_target(first): pass
