"""The launcher starts only the independently configured local query service."""
import contextlib
import importlib.util
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch,Mock

root=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('query_launcher_test',root/'scripts/launcher.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
passed=0
def check(name,condition):
    global passed
    assert condition,name
    passed+=1;print('PASS '+name)

with tempfile.TemporaryDirectory() as temporary:
    base=Path(temporary);work=base/'content-workbench';work.mkdir()
    query=base/'content-query';query.mkdir()
    logs=work/'logs';logs.mkdir()
    config=query/'config.json';state=work/'services.json'
    data={'processes':[]}
    with patch.object(launcher,'ROOT',work),patch.object(launcher,'STATE',state):
        config.write_text(json.dumps({'workflow_connected':False}),encoding='utf-8')
        with patch.object(launcher.subprocess,'Popen') as process:
            launcher.ensure_query_service(data,logs)
            check('unconnected independent query is not started',not process.called)
        config.write_text(json.dumps({'workflow_connected':True}),encoding='utf-8')
        reply=contextlib.nullcontext(io.BytesIO(b'{"application":"content-query"}'))
        with patch.object(launcher.urllib.request,'urlopen',return_value=reply),patch.object(launcher.subprocess,'Popen') as process:
            launcher.ensure_query_service(data,logs)
            check('healthy independent query is reused without duplicate process',not process.called)
        fake_socket=Mock();fake_socket.__enter__=Mock(return_value=fake_socket);fake_socket.__exit__=Mock(return_value=False)
        with patch.object(launcher.urllib.request,'urlopen',side_effect=OSError('offline')),patch.object(launcher.socket,'socket',return_value=fake_socket),patch.object(launcher.subprocess,'Popen',return_value=Mock(pid=12345)) as process,patch.object(launcher,'identity',return_value=987):
            launcher.ensure_query_service(data,logs)
            args,kw=process.call_args
            check('query starts from its own project directory',kw['cwd']==query and 'query_service.app:app' in args[0])
            check('query has an independent module path',kw['env']['PYTHONPATH']==str(query))
            check('query port only binds the loopback',fake_socket.bind.call_args.args[0]==('127.0.0.1',8091))
            check('launched process identity is saved for safe service lifecycle',data['processes']==[{'name':'query','pid':12345,'identity':987}])
            check('service state is persisted',json.loads(state.read_text('utf-8'))['processes']==data['processes'])
        fake_socket.bind.side_effect=OSError('occupied')
        with patch.object(launcher.urllib.request,'urlopen',side_effect=OSError('offline')),patch.object(launcher.socket,'socket',return_value=fake_socket),patch.object(launcher.subprocess,'Popen') as process:
            launcher.ensure_query_service(data,logs)
            check('occupied query port never stops another application',not process.called)
print(f'{passed} 通过 / 0 失败')
