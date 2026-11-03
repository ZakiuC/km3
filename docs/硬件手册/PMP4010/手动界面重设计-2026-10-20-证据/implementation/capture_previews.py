"""Capture the actual production Grid with an offline MockBus fixture only."""
from pathlib import Path
import sys,json,hashlib
sys.path.insert(0,'device/drivers/pmp4010/tools')
from _test_simple import SimpleTests
from pmp4010_simple import SimpleApp
from _test_bench_support import tool
out=Path(__file__).resolve().parent/'previews-final';out.mkdir(parents=True,exist_ok=True)
files=['device/drivers/pmp4010/tools/pmp4010_simple.py','device/drivers/pmp4010/tools/pmp4010_simple_view.py','tools/hwtui/tui.py']
def hashes():return {p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files}
before=hashes()
case=SimpleTests('test_linear_mm_s_exact_speed_default_quantization_and_review_seal');case.setUp()
try:
 case.args.manual_velocity=1000;case.linear_ready('4')
 app=SimpleApp(tool,case.args);app.controller=case.ctl;app.current_view='main';app.progress='会话已授权'
 for rows,cols in ((24,80),(32,120),(20,64)):
  app.rows,app.cols=rows,cols
  for view in ('main','edit','confirm'):
   app.current_view=view
   if view=='edit':app.edit_kind='relative';app.draft='+1.2'
   if view=='confirm':app.action='relative';app.pending=case.ctl.review_action('relative',case.out,'+1.2')
   lines=app.build().to_lines();name=view+'-'+str(cols)+'x'+str(rows)
   (out/(name+'.txt')).write_text('\n'.join(lines)+'\n')
   (out/(name+'.json')).write_text(json.dumps(dict(lines=lines,styles=app._row_styles),ensure_ascii=False,indent=2))
finally:case.tearDown();case.doCleanups()
after=hashes();assert before==after
(out/'source-hashes.json').write_text(json.dumps(dict(before=before,after=after,transport='production SimpleApp + standard MockBus; no hardware'),indent=2)+'\n')
print(out)
