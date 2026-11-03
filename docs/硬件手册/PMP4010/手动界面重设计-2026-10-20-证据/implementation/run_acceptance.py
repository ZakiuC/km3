"""Reproduce local-only final gates and record exact input hashes + raw output."""
from pathlib import Path
import datetime,hashlib,json,subprocess,sys
root=Path.cwd();out=Path(__file__).resolve().parent
paths=['device/drivers/pmp4010/tools/pmp4010_simple.py','device/drivers/pmp4010/tools/pmp4010_simple_view.py','device/drivers/pmp4010/tools/_test_simple.py','device/drivers/pmp4010/tools/_test_simple_pty.py','tools/hwtui/tui.py','tools/hwtui/test_tui.py']
def hashes():return {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
start=hashes();(out/'acceptance-start-sha256.json').write_text(json.dumps(start,indent=2)+'\n')
results=[]
for name,command in [
 ('pmp-python-final',[sys.executable,'-I','-m','unittest','discover','-s','device/drivers/pmp4010/tools','-p','_test_*.py']),
 ('hwtui-python-final',[sys.executable,'-I','-m','unittest','discover','-s','tools/hwtui','-p','test_*.py']),
 ('simple-pty-80x24',[sys.executable,'-I','device/drivers/pmp4010/tools/_test_simple_pty.py','--run-visual',str(out/'pty-80x24')]),
 ('simple-pty-64x20-no-color',[sys.executable,'-I','device/drivers/pmp4010/tools/_test_simple_pty.py','--run-visual-small',str(out/'pty-64x20-no-color')]),
]:
 log=out/(name+'.log')
 if log.exists():log.rename(out/(name+'-before-final-rerun.log'))
 with log.open('w') as stream:
  result=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT)
 results.append(dict(command=command,rc=result.returncode,log=str(log.relative_to(root))))
 print(name,'rc='+str(result.returncode),flush=True)
end=hashes();manifest=dict(source_hashes=end,start_source_hashes=start,inputs_unchanged=start==end,results=results,local_acceptance=dict(status='passed' if start==end and all(r['rc']==0 for r in results) else 'failed',evidence=[r['log'] for r in results]))
(out/'freeze.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
sys.exit(0 if manifest['local_acceptance']['status']=='passed' else 1)
