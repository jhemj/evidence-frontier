import pytest

from workbench import worker


class _ExitedProcess:
    returncode = 0

    def __init__(self, args, stdout, **kwargs):
        stdout.write(_PAYLOAD)
        stdout.flush()

    def poll(self):
        return 0

    def kill(self):
        pass

    def wait(self):
        return 0


_LIMIT = 32 * 1024 * 1024
_PAYLOAD = b''


@pytest.mark.parametrize('size, expected_error', [(_LIMIT + 1, True), (_LIMIT, False)])
def test_command_checks_output_limit_after_process_exit(monkeypatch, size, expected_error):
    global _PAYLOAD
    _PAYLOAD = b'x' * size
    monkeypatch.setattr(worker.shutil, 'which', lambda _: '/fake/tool')
    monkeypatch.setattr(worker.subprocess, 'Popen', _ExitedProcess)

    if expected_error:
        with pytest.raises(TimeoutError, match='출력 예산'):
            worker.command(['fake-tool'])
    else:
        assert len(worker.command(['fake-tool'])) == _LIMIT
