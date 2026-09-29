from contextlib import nullcontext
import json
from pathlib import Path

import pytest

from workbench import worker
from workbench import partition_probe


class FakeVolume:
    def __init__(self, offset, size, header=b''):
        self.offset = offset
        self.size = size
        self._data = header + b'\0' * max(0, 4096 - len(header))
        self._position = 0

    def seek(self, position):
        self._position = position

    def read(self, size=-1):
        data = self._data[self._position:self._position + size]
        self._position += len(data)
        return data


class FakeTarget:
    def __init__(self, volumes):
        self.volumes = volumes
        self.disks = self

    def apply(self):
        pass


def fake_open_target(volumes):
    return lambda *args, **kwargs: nullcontext(FakeTarget(volumes))


def fail_mmls(args, *_, **__):
    if args[0] == 'mmls':
        raise RuntimeError('mmls failed: no partition table')
    raise AssertionError(f'unexpected command: {args!r}')


@pytest.mark.parametrize(
    ('header', 'filesystem'),
    [(b'XFSB', 'xfs'), (b'\0' * 1080 + b'\x53\xef', 'ext')],
)
def test_partition_inventory_falls_back_to_single_filesystem_image(
    tmp_path, monkeypatch, header, filesystem
):
    image = tmp_path / 'Partition3.e01'
    image.write_bytes(b'fixture')
    volume = FakeVolume(0, 8192, header)
    def fake_command(args, **kwargs):
        if args[0] == 'mmls': raise RuntimeError('mmls failed: no partition table')
        return json.dumps({'ok': True, 'probe': {'filesystem': filesystem, 'offset': 0, 'size': 8192}})
    monkeypatch.setattr(worker, 'command', fake_command)
    monkeypatch.setattr('workbench.image_target.open_target', fake_open_target([volume]))

    inventory = worker.partition_inventory(image)

    assert inventory['layout'] == 'single_filesystem_image'
    assert inventory['adapter'] == 'Dissect partition-image header'
    assert inventory['independent_partition_check'] is False
    assert inventory['partitions'] == [{
        'slot': 'image', 'offset_sectors': 0, 'offset': 0, 'size': 8192,
        'description': f'{filesystem} filesystem image; no partition table',
    }]


def test_partition_inventory_rejects_multiple_fallback_volumes(tmp_path, monkeypatch):
    image = tmp_path / 'multi.e01'
    image.write_bytes(b'fixture')
    volumes = [FakeVolume(0, 4096, b'XFSB'), FakeVolume(4096, 4096, b'XFSB')]
    monkeypatch.setattr(worker, 'command', fail_mmls)
    monkeypatch.setattr('workbench.image_target.open_target', fake_open_target(volumes))

    with pytest.raises(RuntimeError, match='no partition table'):
        worker.partition_inventory(image)


def test_partition_inventory_rejects_unknown_fallback_magic(tmp_path, monkeypatch):
    image = tmp_path / 'unknown.e01'
    image.write_bytes(b'fixture')
    monkeypatch.setattr(worker, 'command', fail_mmls)
    monkeypatch.setattr('workbench.image_target.open_target', fake_open_target([FakeVolume(0, 4096)]))

    with pytest.raises(RuntimeError, match='no partition table'):
        worker.partition_inventory(image)


def test_crosscheck_reports_unsupported_for_single_filesystem_fallback(tmp_path, monkeypatch):
    image = tmp_path / 'Partition3.e01'
    image.write_bytes(b'fixture')
    volume = FakeVolume(0, 8192, b'XFSB')
    calls = []
    def fake_command(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == 'mmls': raise RuntimeError('mmls failed: no partition table')
        return '{"ok": true, "probe": {"filesystem": "xfs", "offset": 0, "size": 8192}}'
    monkeypatch.setattr(worker, 'command', fake_command)
    monkeypatch.setattr('workbench.image_target.open_target', fake_open_target([volume]))

    result = worker.execute(tmp_path, 'crosscheck', image.name)

    assert result['status'] == 'unsupported'
    assert result['complete'] is False
    assert '교차 비교' in result['error']


def test_probe_positive_and_negative_volume_scopes(tmp_path, monkeypatch):
    image = tmp_path / 'fixture.e01'
    image.write_bytes(b'fixture')
    monkeypatch.setattr('workbench.partition_probe.open_target', fake_open_target([FakeVolume(0, 8192, b'XFSB')]))
    assert partition_probe.probe(image) == {'filesystem': 'xfs', 'offset': 0, 'size': 8192}
    monkeypatch.setattr('workbench.partition_probe.open_target', fake_open_target([FakeVolume(4096, 8192, b'XFSB')]))
    with pytest.raises(ValueError, match='single offset-zero'):
        partition_probe.probe(image)


def test_parent_delegates_probe_to_subprocess(tmp_path, monkeypatch):
    image = tmp_path / 'fixture.e01'
    image.write_bytes(b'fixture')
    def fake_command(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == 'mmls': raise RuntimeError('mmls failed: no partition table')
        return '{"ok": true, "probe": {"filesystem": "ext", "offset": 0, "size": 4096}}'
    calls = []
    monkeypatch.setattr(worker, 'command', fake_command)
    inventory = worker.partition_inventory(image)
    assert inventory['partitions'][0]['description'].startswith('ext filesystem')
    assert len(calls) == 2 and calls[1][0][1:3] == ['-m', 'workbench.partition_probe']
    assert calls[1][1]['timeout'] == 300
