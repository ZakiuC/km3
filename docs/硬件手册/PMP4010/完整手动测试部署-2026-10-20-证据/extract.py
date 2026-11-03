import capture,json,shlex
stage=json.loads((capture.ROOT/'remote-stage.json').read_text())['stage']
capture.run('extract',capture.SSH+['python3 -I -B - '+shlex.quote(stage)],(capture.ROOT/'extract-remote.py').read_bytes())
