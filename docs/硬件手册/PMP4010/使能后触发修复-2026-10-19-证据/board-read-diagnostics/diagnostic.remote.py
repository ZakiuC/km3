import datetime,hashlib,importlib.util,json,pathlib,signal,sys
sys.dont_write_bytecode=True
def emit(event,data):print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),event=event,data=data),ensure_ascii=False),flush=True)
def stop(signum,frame):raise KeyboardInterrupt('controlled signal '+str(signum))
for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,stop)
p=pathlib.Path('/var/tmp/km3-pmp4010-accept-20261019-54c5cb09/controlled-forward.runner.py')
assert hashlib.sha256(p.read_bytes()).hexdigest()=='54c5cb091b32c42663ca35933e3a6dd7e98eb9bcd3118f1c436e1da0ded7f7c5'
spec=importlib.util.spec_from_file_location('frozen_runner',p);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
mods=r.load_payload(r.EXPECTED_PAYLOAD);tool=mods['pmp4010']
cfg,cfgpath=tool.load_device_json();item,_=tool.find_can_bus(cfg,'can-2');assert item['iface']=='can1'
emit('identity',dict(config_path=cfgpath,config_sha256=hashlib.sha256(pathlib.Path(cfgpath).read_bytes()).hexdigest(),device_id=cfg.get('device_id'),boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),payload=str(r.EXPECTED_PAYLOAD),manifest_sha256=r.EXPECTED_MANIFEST,scope='disabled-state diagnostic only; no prior-motion dynamic inference'))
emit('owner',r.passive_owner_check(0))
initial=[(0x6040,0,2,False),(0x6041,0,2,False),(0x6060,0,1,True),(0x6061,0,1,True),(0x603F,0,2,False),(0x607C,0,4,True),(0x6064,0,4,True)]
extra=[(0x6067,0,4,False),(0x6068,0,2,False),(0x2006,0x12,2,False),(0x606B,0,4,True),(0x6069,0,4,True),(0x606C,0,4,True),(0x6063,0,4,True),(0x60F4,0,4,True),(0x6065,0,4,False),(0x6066,0,2,False),(0x6062,0,4,True),(0x607A,0,4,True)]
final=[(0x6041,0,2,False),(0x6064,0,4,True),(0x606C,0,4,True),(0x603F,0,2,False)]
bus=None;client=None;count=0;failure=None
try:
 bus=tool.connect_bus('can1');client=tool._canopen.SdoClient(bus,1,timeout=0.1)
 for phase,items in [('initial',initial),('disabled_diagnostic',extra),('final',final)]:
  for index,sub,width,signed in items:
   started=datetime.datetime.now(datetime.timezone.utc).isoformat();raw=client.read(index,sub);count+=1
   assert len(raw)==width,('unexpected width',hex(index),sub,len(raw),width)
   val=int.from_bytes(raw,'little',signed=signed)
   emit('upload',dict(phase=phase,index=hex(index),subindex=hex(sub),expected_width=width,width=len(raw),signed=signed,raw_hex=raw.hex(),value=val,started_utc=started,quarantined=client.quarantined))
   if index==0x6041 and (val&4 or val&8):raise ValueError('enabled or fault status: stop reads without control')
   if index==0x603F and val:raise ValueError('nonzero fault object: stop reads without control')
except BaseException as exc:
 failure=type(exc).__name__+': '+str(exc);emit('failure',dict(reason=failure,quarantined=getattr(client,'quarantined',None),retry=0))
finally:
 if bus is not None:bus.close();emit('bus_closed',dict(control_writes=0,NMT=0,downloads=0))
 emit('FINAL',dict(success=failure is None,failure=failure,uploads_completed=count,downloads=0,NMT=0,retries=0,clients_created=1 if client else 0,quarantined=getattr(client,'quarantined',None)))
sys.exit(1 if failure else 0)
