"""Shell syntax must not manufacture file identities from inner slash fragments."""
import pytest
from workbench.linux_analysis import normalize_command


def absolute(command, cwd=None):
    return [r['absolute'] for r in normalize_command(command, cwd) if r['absolute']]


def test_cron_step_and_sed_program_are_not_paths():
    assert absolute('*/7 * * * * /opt/worker >/dev/null 2>&1') == ['/opt/worker', '/dev/null']
    assert absolute("IP=$(who am i | awk '{print $5}' | sed 's/[()]//g')") == []
    assert absolute("sed '/tmp/d' /data/input") == ['/data/input']
    assert absolute("awk '/var/ {print $1}' /data/input") == ['/data/input']


def test_tilde_and_variables_never_resolve_using_examiner_context(monkeypatch):
    monkeypatch.setenv('HOME', '/examiner')
    rows = normalize_command('cat /dev/null > ~/.audit; cat "$HOME/config" "${ROOT}/var/log" ~service/data')
    assert [r['absolute'] for r in rows] == ['/dev/null', None, None, None, None]
    assert [r['original'] for r in rows][1:] == ['~/.audit', '$HOME/config', '${ROOT}/var/log', '~service/data']
    assert '/.audit' not in absolute('cat /dev/null > ~/.audit')


def test_literal_paths_and_identity_keep_names_including_numeric_names():
    assert absolute('/1 --output="/opt/my output.txt" ./agent ../config', '/srv/task') == [
        '/1', '/opt/my output.txt', '/srv/task/agent', '/srv/config']
    assert absolute('file="/etc/loader.conf"; cat "$file"') == ['/etc/loader.conf']
    assert absolute('curl https://example.invalid/a/b') == []
    assert absolute('ls /tmp/*.log') == []


@pytest.mark.parametrize('command', ["sed -e '/tmp/d' /data/input", "sed -n -f /opt/filter.sed /data/input",
                                    'awk -f/opt/filter.awk /data/input'])
def test_script_file_operands_remain_paths(command):
    paths = absolute(command)
    assert '/data/input' in paths
    assert '/tmp/d' not in paths
    if '-f' in command:
        assert any(p.startswith('/opt/filter.') for p in paths)


def test_unbalanced_or_embedded_shell_code_does_not_invent_inner_paths():
    assert absolute('echo "unterminated /tmp/fragment') == []
    assert absolute('sh -c "/opt/first; /opt/second"') == []
    assert normalize_command('./agent')[0]['absolute'] is None
