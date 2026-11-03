#!/usr/bin/env python3
"""Draw actual Grid preview JSON in fixed terminal cells (no invented UI)."""
import argparse,hashlib,json,pathlib,unicodedata
from PIL import Image,ImageDraw,ImageFont
P=pathlib.Path(__file__).resolve().parent;E=P.parent
parser=argparse.ArgumentParser();parser.add_argument('inputs',nargs='+');parser.add_argument('--tag',required=True);a=parser.parse_args()
out=P/('previews-'+a.tag);out.mkdir(exist_ok=False)
fontpath='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
font=ImageFont.truetype(fontpath,24,index=7);bold=ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',24,index=7)
# These terminal RGB values correspond to simple_view.PALETTE SGR attributes.
colors={'title':'#75d7e8','section':'#63c4d4','metric':'#d9e0eb','ok':'#78ca83','warning':'#e9c66a','danger':'#f17078','muted':'#7d8798','action':'#75d7e8'}
records=[]
for arg in a.inputs:
 p=pathlib.Path(arg);raw=p.read_bytes();v=json.loads(raw);lines=v['lines'];styles=v['styles']
 width=max(sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in line) for line in lines)
 im=Image.new('RGB',(width*12+32,len(lines)*28+32),'#10151e');draw=ImageDraw.Draw(im)
 for y,line in enumerate(lines):
  tone=styles.get(str(y));color=colors.get(tone,'#d9e0eb');x=16
  for c in line:
   draw.text((x,16+y*28),c,font=bold if tone in ('title','metric','action','warning','danger') else font,fill=color,stroke_width=0)
   x+=12*(2 if unicodedata.east_asian_width(c) in 'WF' else 1)
 target=out/(p.stem+'.png');im.save(target)
 records.append({'source':str(p),'source_sha256':hashlib.sha256(raw).hexdigest(),'output':str(target),'columns':width,'rows':len(lines),'renderer':'Pillow fixed cells from actual Grid JSON; terminal emulator colors may differ'})
(out/'render-manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(records,ensure_ascii=False,indent=2))
