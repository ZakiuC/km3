import capture,json,pathlib
stage=capture.run('create-stage',capture.SSH+['python3 -I -B -'],b"import tempfile;print(tempfile.mkdtemp(prefix='.manual-test-e137c603-',dir='/opt/km3/hwtools'))\n").stdout.decode().strip()
assert stage.startswith('/opt/km3/hwtools/.manual-test-e137c603-') and len(stage.split('/'))==5
(capture.ROOT/'remote-stage.json').write_text(json.dumps({'stage':stage})+'\n')
archive=json.loads((capture.ROOT/'archive-validation.json').read_text())['archive']
argv=['scp','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=5',archive,'root@192.168.0.188:'+stage+'/payload.tar.gz']
capture.run('upload',argv)
