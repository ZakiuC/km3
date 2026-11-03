from pathlib import Path
import importlib.util,json,shlex
b=Path(__file__).parent
spec=importlib.util.spec_from_file_location('remote_record',b/'remote_record.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
code=(b/'stage_frozen.py').read_text().replace('e5e6be59dbed7611f3b4e5f6d5c53e38e2f7e1527afa1831e7b511e4a0fcacb6','a1bfb7b491ec1ff0509992db6ecae3b68c80384d4db59d068cffa47960f31f6c').replace('km3-pp-disabled-start-readonly-20261019-','km3-pp-disabled-start-probe-20261019-')
(b/'stage_probe_remote.py').write_text(code)
p=r.run('stage-probe-package','python3 -I -B -c '+shlex.quote(code),Path('/tmp/km3-pp-disabled-start-package-20261019/pmp4010-board-payload.tar.gz').read_bytes())
raise SystemExit(p.returncode)
