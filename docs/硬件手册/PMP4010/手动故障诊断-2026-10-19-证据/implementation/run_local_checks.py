"""Offline affected-suite evidence; no socket, SSH, CAN interface or hardware."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
results = []
for name in ('manual', 'simple'):
    argv = [sys.executable, '-B', '-I', '-m', 'unittest', 'discover', '-s',
            'device/drivers/pmp4010/tools', '-p', '_test_' + name + '.py', '-v']
    output = HERE / (name + '-unittest.raw.txt')
    with output.open('w', encoding='utf-8') as stream:
        result = subprocess.run(argv, cwd=REPO, stdout=stream, stderr=subprocess.STDOUT)
    results.append(dict(argv=argv, cwd=str(REPO), rc=result.returncode, raw_output=output.name))
    print(name, 'rc=' + str(result.returncode), flush=True)
paths = ['device/drivers/pmp4010/tools/' + name for name in
         ('pmp4010_manual.py', 'pmp4010_simple.py', '_test_manual.py', '_test_simple.py')]
manifest = dict(checks=results, sha256={path:hashlib.sha256((REPO/path).read_bytes()).hexdigest() for path in paths},
                route=dict(requested_role='laborer', requested_model='gpt-6.1-sol', requested_effort='high',
                           actual_model='unknown', actual_effort='unknown', last_token_usage_input_tokens='unknown',
                           fork_turns='none', input_estimate='<20K at dispatch; bounded route latest estimate <130K from parent, neither measured'))
(HERE/'verification.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
raise SystemExit(any(result['rc'] for result in results))
