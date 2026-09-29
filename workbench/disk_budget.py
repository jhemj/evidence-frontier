"""Output-filesystem admission checks, not a reservation against other processes."""
import os
import shutil
from pathlib import Path


def require_space(plans, reserve=None):
    reserve = int(os.getenv('FRONTIER_DISK_RESERVE_BYTES', str(512 * 1024 * 1024))) if reserve is None else reserve
    if reserve < 0:
        raise ValueError('Disk reserve must be nonnegative')
    volumes = {}
    for destination, amount in plans:
        if not isinstance(amount, int) or amount < 0:
            raise ValueError('Invalid output byte budget')
        path = Path(destination).resolve()
        while not path.exists():
            path = path.parent
        try:
            device = path.stat().st_dev
            free = shutil.disk_usage(path).free
        except OSError as exc:
            raise ValueError('출력 디스크 여유량 확인 실패; 쓰기를 시작하지 않습니다.') from exc
        row = volumes.setdefault(device, {'path': str(path), 'planned_bytes': 0, 'free_bytes': free})
        row['planned_bytes'] += amount
        row['free_bytes'] = min(row['free_bytes'], free)
    for row in volumes.values():
        row['reserve_bytes'] = reserve
        if row['free_bytes'] < row['planned_bytes'] + reserve:
            raise ValueError('출력 디스크 공간 부족: 예상 출력과 보존 여유량을 확보한 뒤 재시도하세요.')
    return list(volumes.values())
