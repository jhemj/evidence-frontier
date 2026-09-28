"""Run in a bounded subprocess so parser failures cannot take down the controller."""
import json
import sys
from importlib.metadata import version
from .image_target import open_target


def probe(path):
    with open_target(path) as target:
        return {'tool':'dissect.target','version':version('dissect.target'),
                'volumes':[{'name':v.name,'offset':v.offset,'size':v.size,'filesystem':v.fs.__type__ if v.fs else None} for v in target.volumes]}

if __name__=='__main__':
    print(json.dumps(probe(sys.argv[1]),default=str))
