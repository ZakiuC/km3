import datetime,hashlib,json,pathlib,socket,sys
stage=pathlib.Path('/var/tmp/km3-pmp4010-alarm-20261019-735538978204/motion-compare-proposal')
expected={'motion_compare_once.py': 'e530b81f231d3ccccca5df741ccc0f79ec410b9cf5569e517d14428ca7151202', 'pinned_payload.py': 'd8d99ccffdf1b1cb3fc02669af2627243f9801b4fe8861cc39f18692af954a62'}
for name,sha in expected.items():
 assert (stage/name).resolve(strict=True).parent==stage
 assert hashlib.sha256((stage/name).read_bytes()).hexdigest()==sha
sys.path.insert(0,str(stage))
def prohibited(*args,**kwargs):raise RuntimeError('冷加载禁止socket')
socket.socket=prohibited
from pinned_payload import load_pinned,ROOT
load_pinned(ROOT)
sys.modules['pmp4010_velocity'].require_velocity_library()
print(json.dumps(dict(event='ARM_library_cold_load_success',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),CAN_opens=0,CAN_sends=0)))
