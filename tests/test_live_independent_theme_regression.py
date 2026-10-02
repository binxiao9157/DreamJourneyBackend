import json,unittest
from contextlib import nullcontext
from types import SimpleNamespace as NS
from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemeAssembler
from app.services.owner_truth_live_theme_provider import DeepSeekLiveThemeProvider
from app.domain.owner_truth.live_topics import SupportedLiveTheme,digest
from app.core.config import Settings

class IndependentThemeRegression(unittest.TestCase):
 def test_supported_independent_theme_must_remain_publishable(self):
  atom=dict(atomId='new',content={'summary':'合成的新事件'},contentHash=digest('new'),supportState='supported',dimensions=['dailyLife'],evidenceIds=['e'],evidence=[{'text':'合成的新事件'}])
  target=dict(topicId='old-topic',version=1,proposalHash=digest('old'),state='pending',atoms=[dict(atomId='old',content={'summary':'另一独立事件'})])
  theme=SupportedLiveTheme('new','新事件','合成的新事件',('new',),('e',),('dailyLife',),digest('support'))
  class Probe(RecoveryThemeAssembler):
   def _uow(self,*args):return nullcontext()
   def _admit(self,*args):return None
   def _call(self,**kw):
    stage,request=kw['prepared'];data=json.loads(request.payload()['messages'][1]['content'])
    if stage=='themeRelation':payload=dict(schemaVersion='owner-truth-live-theme-relation-v1',inputHash=data['inputHash'],relation='none',targetTopicId=None)
    else:payload=dict(schemaVersion='owner-truth-live-theme-relation-support-v1',inputHash=data['inputHash'],proposalHash=data['proposalHash'],verdict='supported',sameSubjectEvent=False,compatibleTime=True,correctionsResolved=False,summarySupported=True)
    kw['validator'](payload);return payload
  repo=NS(relation_catalog=lambda **kw:[target]);host=NS(_settings=Settings(deepseek_api_key='synthetic'),_store=NS(owner_truth_live_topic_repository=lambda:repo))
  assembler=Probe(host)
  result=assembler.relate_themes(lease=None,intent=NS(target=NS(vault_id='v',owner_subject_id='o')),source=NS(source_id='s',source_metadata={'snapshotRevision':1}),run_id='r',themes=[theme],catalog=[atom])
  print(json.dumps(dict(acceptedThemes=len(result[0]),blocked=result[2],controlledResponse='same enum and boolean values observed on device scene; no provider or database called')),flush=True)
  self.assertEqual([t.key for t in result[0]],['new'],'independently supported none relation must not suppress a valid new theme')
if __name__=='__main__':unittest.main()
