"""Bounded child-process probe for partition-less filesystem images."""
import json
import sys
from pathlib import Path
from .image_target import open_target


def probe(path):
    with open_target(path, apply=False) as target:
        target.disks.apply()
        volumes = list(target.volumes)
        if len(volumes) != 1 or volumes[0].offset != 0:
            raise ValueError('not a single offset-zero volume')
        volume = volumes[0]
        volume.seek(0)
        header = volume.read(4096)
        filesystem = ('xfs' if header[:4] == b'XFSB' else
                      'ext' if header[1080:1082] == b'\x53\xef' else None)
        if filesystem is None:
            raise ValueError('unsupported filesystem magic')
        return {'filesystem': filesystem, 'offset': 0, 'size': volume.size}


def main():
    try:
        print(json.dumps({'ok': True, 'probe': probe(Path(sys.argv[1]))}))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)[:500]}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
