import datetime,json,pathlib
p=pathlib.Path('/var/tmp/km3-pmp4010-alarm-20261019-735538978204/motion-compare-proposal/execution.attempt')
result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),execution_attempt_exists=p.exists() or p.is_symlink(),CAN_opens=0,CAN_sends=0)
print(json.dumps(result))
raise SystemExit(1 if result['execution_attempt_exists'] else 0)
