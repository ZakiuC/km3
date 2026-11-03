import datetime, json, pathlib, subprocess, sys, time
root=pathlib.Path(__file__).resolve().parent
label=sys.argv[1]
argv=sys.argv[2:]
meta={"argv":argv,"cwd":str(pathlib.Path.cwd()),"started_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()}
(root/(label+".command.json")).write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n")
start=time.monotonic()
with (root/(label+".stdout")).open("wb") as out, (root/(label+".stderr")).open("wb") as err:
    result=subprocess.run(argv,stdout=out,stderr=err)
meta.update(returncode=result.returncode,elapsed_seconds=time.monotonic()-start,finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
(root/(label+".result.json")).write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n")
(root/(label+".rc")).write_text(str(result.returncode)+"\n")
print(json.dumps(meta,ensure_ascii=False))
print("--- stdout ---")
print((root/(label+".stdout")).read_text(errors="replace"))
print("--- stderr ---")
print((root/(label+".stderr")).read_text(errors="replace"))
sys.exit(result.returncode)
