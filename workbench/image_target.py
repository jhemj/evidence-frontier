"""Open only explicitly validated EWF segments, with read-only handle ownership.

Dissect's automatic EWF glob can include same-stem acquisition sidecars (.txt).
Passing open handles bypasses that glob without touching the original directory.
"""
from contextlib import ExitStack, contextmanager
from pathlib import Path


@contextmanager
def open_target(path, *, apply=True):
    from dissect.target import Target
    from .worker import segments
    path = Path(path)
    with ExitStack() as stack:
        if path.suffix.lower() in ('.e01', '.ex01'):
            from dissect.target.containers.ewf import EwfContainer
            handles = []
            for number, part in enumerate(segments(path), 1):
                if part.is_symlink() or not part.is_file():
                    raise ValueError('EWF segment must be a regular non-symlink file')
                fh = stack.enter_context(part.open('rb'))
                header = fh.read(13)
                if len(header) != 13 or header[:8] != b'EVF\x09\x0d\x0a\xff\x00' or int.from_bytes(header[9:11], 'little') != number:
                    raise ValueError(f'Invalid EWF signature or segment number: {part.name}')
                fh.seek(0)
                handles.append(fh)
            container = EwfContainer(handles)
            stack.callback(container.close)
            target = Target(path)
            target.disks.add(container)
            if apply:
                target.apply()
        else:
            target = Target.open(str(path)) if apply else Target.open(str(path), apply=False)
            for disk in target.disks:
                stack.callback(disk.close)
        yield target
