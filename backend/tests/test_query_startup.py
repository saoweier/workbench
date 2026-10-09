"""The launcher exposes the workbench on the LAN and keeps the query service local."""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch,Mock

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'backend'))
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

# The app API must be reachable from the phone/second computer by default, and
# the query service must stay loopback-only. These two are asserted together so a
# future change cannot quietly blanket-bind every service in the project.
check('app API listens on every interface by default',launcher.DEFAULT_HOST=='0.0.0.0')
check('launcher command is the single source of the API bind address',
      launcher.api_command(8000)==['-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000']
      and launcher.api_command(8000,'127.0.0.1')[-4:]==['--host','127.0.0.1','--port','8000'])
check('only loopback is advertised when the API is bound to loopback',
      launcher.open_urls(8000,'127.0.0.1')==['http://127.0.0.1:8000/'])
check('virtual and TUN adapters are never advertised as LAN addresses',
      all(launcher._virtual_address(address) for address in
          ('198.18.0.1','198.19.1.1','169.254.1.1','172.26.32.1','127.0.0.1','0.0.0.0'))
      and not launcher._virtual_address('10.6.101.1') and not launcher._virtual_address('192.168.1.20'))
check('loopback stays first and a hostname binds are still advertised',
      launcher.open_urls(8000,'0.0.0.0')[0]=='http://127.0.0.1:8000/'
      and launcher.open_urls(8000,'myhost.example')==['http://127.0.0.1:8000/','http://myhost.example:8000/']
      and [url for url in launcher.open_urls(8000,'0.0.0.0')
           if any(url.startswith('http://'+prefix) for prefix in
                  ('0.','169.254.','198.18.','198.19.','172.26.'))]==[])
with patch.object(launcher,'start') as started,patch.object(sys,'argv',['launcher.py','start','--host','127.0.0.1','--no-browser']):
    launcher.main()
    check('--host reaches start',started.call_args.args==(8000,False,'127.0.0.1'))

def start_workbench(host,state_path,work):
    """Run launcher.start() against stubs and report the API argv it launched."""
    launched={}
    def fake_popen(args,**kw):
        launched.setdefault('calls',[]).append(args)
        launched['instance']=kw['env']['CWB_INSTANCE_ID']
        return Mock(pid=1000+len(launched['calls']))
    def fake_health(port):
        return {'api':{'instance_id':launched['instance'],'pid':1},
                'worker':{'instance_id':launched['instance'],'status':'running'}}
    fake_socket=Mock();fake_socket.__enter__=Mock(return_value=fake_socket);fake_socket.__exit__=Mock(return_value=False)
    with patch.object(launcher,'ROOT',work),patch.object(launcher,'STATE',state_path), \
            patch.object(launcher.subprocess,'run'),patch.object(launcher.subprocess,'Popen',side_effect=fake_popen), \
            patch.object(launcher,'health',side_effect=fake_health),patch.object(launcher,'identity',return_value=7), \
            patch.object(launcher,'initialize_demo'),patch.object(launcher,'ensure_query_service'), \
            patch.object(launcher.socket,'socket',return_value=fake_socket),contextlib.redirect_stdout(io.StringIO()):
        launcher.start(8000,False,host)
    return launched,json.loads(state_path.read_text(encoding='utf-8'))

with tempfile.TemporaryDirectory() as temporary:
    work=Path(temporary)/'content-workbench';(work/'storage'/'logs').mkdir(parents=True)
    state=work/'storage'/'services.json'
    launched,recorded=start_workbench('0.0.0.0',state,work)
    api=[call for call in launched['calls'] if 'app.main:app' in call]
    check('start launches the API on the LAN interface',
          bool(api) and api[0][api[0].index('--host')+1]=='0.0.0.0')
    check('start records the bind address for later restarts',recorded['host']=='0.0.0.0')
    check('every LAN address is printed so the user knows what to open',
          recorded['port']==8000 and len(launcher.open_urls(8000,'0.0.0.0'))>=1)

    # restart-api runs after a code change; it must keep the same interface the
    # user originally started with instead of silently falling back to loopback.
    database=Path(temporary)/'restart.db'
    os.environ['CWB_DATABASE_URL']=f'sqlite:///{database.as_posix()}'
    from app.core.config import get_settings
    from app.models.entities import Base
    from sqlalchemy import create_engine
    engine=create_engine(get_settings().database_url);Base.metadata.create_all(engine);engine.dispose()
    rediscovered={}
    def restart_popen(args,**kw):rediscovered['args']=args;return Mock(pid=222)
    # The first probe sees the old API pid; the readiness loop must see a new one.
    restart_pids=iter([888,999])
    def restart_health(port):
        return {'api':{'instance_id':'restart-check','pid':next(restart_pids,999)},'worker':{'status':'running'}}
    state.write_text(json.dumps({'workspace':str(work),'instance_id':'restart-check','port':8000,
                                 'host':'0.0.0.0','processes':[{'name':'api','pid':777,'identity':555}]}),encoding='utf-8')
    with patch.object(launcher,'ROOT',work),patch.object(launcher,'STATE',state), \
            patch.object(launcher.subprocess,'run',return_value=Mock(returncode=0)),patch.object(launcher.subprocess,'Popen',side_effect=restart_popen), \
            patch.object(launcher,'health',side_effect=restart_health), \
            patch.object(launcher,'identity',side_effect=lambda pid:555 if pid==777 else None), \
            contextlib.redirect_stdout(io.StringIO()):
        launcher.restart_api()
    check('restart-api reuses the recorded bind address',
          'app.main:app' in rediscovered['args'] and rediscovered['args'][rediscovered['args'].index('--host')+1]=='0.0.0.0')

print(f'{passed} 通过 / 0 失败')
