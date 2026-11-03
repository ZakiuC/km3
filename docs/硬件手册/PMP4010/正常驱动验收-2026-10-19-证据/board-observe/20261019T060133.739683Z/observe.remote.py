import contextlib,io,json
_buf=io.StringIO()
with contextlib.redirect_stdout(_buf):
 exec('"""One passive OS/filesystem snapshot: no CAN socket, SDO, remote writes or process control."""\nimport datetime,hashlib,json,os,pathlib,socket,subprocess,sys\nP=pathlib.Path\nstage=P(sys.argv[1])\nassert stage.parent==P("/var/tmp") and stage.name.startswith("km3-pmp4010-state-20261019-")\ndef run(args):\n try:\n  p=subprocess.run(args,capture_output=True,text=True,timeout=5)\n  return dict(argv=args,rc=p.returncode,stdout=p.stdout,stderr=p.stderr)\n except Exception as e:return dict(argv=args,error=str(e))\ndef read(p):\n try:return p.read_text()\n except (OSError,UnicodeError) as e:return dict(error=str(e))\ndef sha(p):\n try:return hashlib.sha256(p.read_bytes()).hexdigest()\n except OSError as e:return dict(error=str(e))\nservices={s:run([\'systemctl\',\'show\',s,\'--property=MainPID,ActiveState,SubState,ExecMainStartTimestampMonotonic,ActiveEnterTimestampMonotonic\']) for s in (\'km3-core\',\'km3-rt-controller\',\'km3-video\')}\nprocesses=[]\nfor p in P(\'/proc\').iterdir():\n if not p.name.isdigit() or int(p.name)==os.getpid():continue\n try:cmd=(p/\'cmdline\').read_bytes().replace(b\'\\x00\',b\' \').decode(errors=\'replace\')\n except OSError:continue\n if any(s in cmd for s in (\'pmp4010\',\'pty_runner\',\'mock_app\',\'km3-pp\',\'drive_probe\',\'board_app\',\'candump\',\'cansend\',\'cangen\',\'canplayer\',\'km3-rt-controller\')):\n  fds={}\n  try:\n   for f in (p/\'fd\').iterdir():\n    try:fds[f.name]=os.readlink(f)\n    except OSError as e:fds[f.name]=dict(error=str(e))\n  except OSError as e:fds=dict(error=str(e))\n  processes.append(dict(pid=int(p.name),cmdline=cmd,fds=fds))\ncfg=P(\'/etc/km3/device.json\')\ntry:device_id=json.loads(cfg.read_text()).get(\'device\',{}).get(\'id\')\nexcept Exception as e:device_id=dict(error=str(e))\ntry:model=P(\'/proc/device-tree/model\').read_bytes().rstrip(b\'\\x00\').decode()\nexcept OSError as e:model=dict(error=str(e))\nprint(json.dumps(dict(stage_path=str(stage),stage_exists=stage.exists(),stage_realpath=os.path.realpath(stage),utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),hostname=socket.gethostname(),model=model,boot_id=read(P(\'/proc/sys/kernel/random/boot_id\')),uptime=read(P(\'/proc/uptime\')),device_id=device_id,config_sha=sha(cfg),can_env_sha=sha(P(\'/etc/km3/can.env\')),current=os.path.realpath(\'/opt/km3/hwtools/current\'),services=services,related_processes_excluding_snapshot_pid=processes,snapshot_pid=os.getpid(),can_rcvlists={p.name:read(p) for p in P(\'/proc/net/can\').glob(\'rcvlist*\')},can_link=run([\'ip\',\'-details\',\'-statistics\',\'-json\',\'link\',\'show\',\'can1\']),remote_file_writes=0,CAN_socket_opens=0,CAN_sends=0,process_control_operations=0),ensure_ascii=False,indent=2))\n',globals())
out=json.loads(_buf.getvalue())

log_dirs=[P('/var/tmp/km3-pmp4010-state-20261019-43b0b2355c3b/payload/logs'),P('/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab/payload/logs')]
observed_logs={}
for root in log_dirs:
 records=[]
 if root.is_dir():
  for p in sorted(root.rglob('*')):
   if p.is_symlink() or not p.is_file() or root not in p.resolve().parents:continue
   s=p.stat()
   records.append(dict(path=str(p),bytes=s.st_size,mtime_ns=s.st_mtime_ns,sha256=sha(p) if s.st_size<20000000 else 'not_hashed_size_limit'))
 else:records.append(dict(error='log_directory_missing'))
 observed_logs[str(root)]=records
out['observed_logs']=observed_logs
out['stage_source_hashes']={str(p):sha(p) for p in [stage/'payload/drivers/pmp4010/tools/pmp4010_manual.py',stage/'payload/drivers/pmp4010/tools/pmp4010_simple.py',stage/'payload/payload-manifest.json']}
out['can_stats']=read(P('/proc/net/can/stats'))
print(json.dumps(out,ensure_ascii=False,indent=2))
