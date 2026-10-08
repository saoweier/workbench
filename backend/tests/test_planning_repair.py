"""Completed planning citation failures get exactly one bounded correction."""
import os,sys,tempfile,types
from pathlib import Path
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
tmp=Path(tempfile.mkdtemp(prefix='cwb-plan-repair-'))
os.environ.update(CWB_STORAGE_ROOT=str(tmp),CWB_DATABASE_URL=f"sqlite:///{tmp/'test.db'}",CWB_SECRET_STORE_PATH=str(tmp/'secrets.json'))
from fastapi.testclient import TestClient
from app.main import app
from app.api.production import SessionFactory
from app.services.content_skills import ContentSkills
from app.services.provider_contract import RunMode
from app.services.adapters.base import AdapterResult
from app.core.errors import ValidationFailed,StateConflict

TestClient(app);passed=0
def check(name,value):
 global passed
 assert value,name
 passed+=1;print('PASS '+name)
good={'audience':'普通读者','objective':'给出实际整理步骤','required_elements':['操作方法'],'acceptance_checks':['回答问题'],
 'pages':[{'index':1,'heading':'整理周末清单','purpose':'给出实际做法','points':['整理待办事项','逐项确定时间'],'visual_type':'diagram','visual_brief':'步骤图','claim_ids':['C01']}]}
bad=deepcopy(good);bad['pages'][0]['claim_ids']=['W02','C99']
class Runtime:
 def __init__(self,outputs):self.outputs=outputs;self.calls=[]
 def search_provider(self):return None
 def complete_text(self,**kw):
  self.calls.append(kw);item=self.outputs[len(self.calls)-1]
  if isinstance(item,Exception):raise item
  return types.SimpleNamespace(id='mock-'+str(len(self.calls))),item if isinstance(item,AdapterResult) else AdapterResult(ok=True,parsed=deepcopy(item))
def run(rt,key):
 return ContentSkills(SessionFactory,rt).plan(topic='整理周末清单',requirements='',claims=[{'id':'C01','statement':'待办事项可整理'}],sources=[],run_mode=RunMode.REAL,context_id=key)
rt=Runtime([bad,good]);result=run(rt,'corrected')
check('known completed invalid reply receives one correction',len(rt.calls)==2)
check('correction yields valid references',result.pages[0].claim_ids==['C01'])
check('original output is never mutated',bad['pages'][0]['claim_ids']==['W02','C99'])
check('correction prompt retains specific invalid IDs','"invalid_claim_ids": ["C99", "W02"]' in rt.calls[1]['prompt'])
check('corrective call has distinct input',rt.calls[0]['prompt']!=rt.calls[1]['prompt'])
from app.models.entities import Event
with SessionFactory() as s:rows=[e.payload for e in s.query(Event).filter_by(entity_type='skill_execution',entity_id='corrected')]
check('failed and corrected stages remain traceable',any(h['state']=='failed' for h in rows) and any(h['state']=='succeeded' for h in rows))
rt=Runtime([bad,bad])
try:run(rt,'twice');okay=False
except ValidationFailed:okay=True
check('second invalid result stops with two total calls',okay and len(rt.calls)==2)
rt=Runtime([good]);run(rt,'valid')
check('valid response never receives correction',len(rt.calls)==1)
for failure in [StateConflict('unknown response'),AdapterResult(ok=False,error_code='timeout',error_message='unknown result')]:
 rt=Runtime([failure])
 try:run(rt,'unknown-'+str(passed));okay=False
 except StateConflict:okay=True
 check('unknown or transport failure is never resent',okay and len(rt.calls)==1)
print(f'结果：{passed} 通过 / 0 失败')
