import capture
script=b"""import sys,pathlib,hashlib,json,os,platform
assert sys.version_info >= (3,9), 'frozen helper requires Python >=3.9'
p=pathlib.Path
snapshot={"python":sys.version,"machine":platform.machine(),"hostname":p('/proc/sys/kernel/hostname').read_text().strip(),"model":p('/proc/device-tree/model').read_bytes().replace(bytes([0]), bytes()).decode(),"configs":{x:hashlib.sha256(p(x).read_bytes()).hexdigest() for x in ['/etc/km3/device.json','/etc/km3/can.env']},"current_link":os.readlink('/opt/km3/hwtools/current'),"current_resolved":str(p('/opt/km3/hwtools/current').resolve()),"final_exists":p('/opt/km3/hwtools/manual-test-20261020-e137c603').exists()}
config=json.loads(p('/etc/km3/device.json').read_text())
def mappings(obj,path=''):
 if isinstance(obj,dict):
  for k,v in obj.items():
   if 'can' in str(k).lower(): print('can_config',path+'/'+k,json.dumps(v,ensure_ascii=False))
   else:mappings(v,path+'/'+k)
 elif isinstance(obj,list):
  for i,v in enumerate(obj):mappings(v,path+'/'+str(i))
mappings(config)
for line in p('/etc/km3/can.env').read_text().splitlines():
 if any(x in line.lower() for x in ['can','bitrate']): print('can_env',line)
print('SNAPSHOT_JSON='+json.dumps(snapshot,ensure_ascii=False))
"""
capture.run('preflight-corrected',capture.SSH+['python3 -I -B -'],script)
