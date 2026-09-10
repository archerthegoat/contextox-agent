"""Synthetic browser fixture. No real Provider or Keychain access."""
import json
import argparse
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4
import uvicorn
from contextox import agent, conversation_store, local_settings
from contextox.api import create_app
from contextox.models import (ProviderConfigSnapshot, SourceIdentity, UpdateDefinitionDraftCall,
    CreateClarificationCall, FinishRunCall, canonical_sha256)
from contextox.provider import ProviderCompletion, ProviderUsage
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_clarifications import field

class SyntheticProvider:
    config=ProviderConfigSnapshot(endpoint_id='deepseek_chat_completions',model='deepseek-v4-flash',thinking='disabled',reasoning_effort=None)
    def complete(self,messages,**kwargs):
        context=json.loads(messages[1]['content']); item=context['input']; content=item['content']
        for _ in range(10 if '慢一点' in content else 2):
            if kwargs['cancel_event'].wait(.2):
                from contextox.provider import ProviderError
                raise ProviderError('cancelled','cancelled')
        output={'public_reply':'【合成验收】可以围绕当前资料核对订单金额。请说明你想解决的问题；退款与缺失金额规则仍需业务确认。','next_action':'discuss'}
        if context.get('mission'):
            output['public_reply']='【合成验收】退款规则会影响统计金额；请核对下方候选卡片。未提供的回答来源与依据将保留为空。'
            if '退款' in content and '扣除' in content:
                suggestions=[]
                for case in context['mission']['clarification_cases']:
                    for i,q in enumerate(case['request']['questions']):
                        suggestions.append({'origin_run_id':case['request']['run_id'],'clarification_id':case['request']['clarification_id'],'request_sha256':case['request_sha256'],'question_index':i,'disposition':'answered','answer':'扣除已退款金额' if i==0 else '缺失金额单独列出','respondent':'合成业务负责人' if '负责人' in content else None,'basis':'本条合成验收回答' if '依据' in content else None,'evidence_refs':[],'targets':[]})
                output['answer_suggestions']=suggestions
        elif conversation_store.is_explicit_work_instruction(content) and context['source_refs']:
            output.update(public_reply='【合成验收】目标与资料范围已明确，开始整理候选。',next_action='start_task',title='按地区统计订单金额',goal={'text':content,'message_refs':[{'message_id':item['message_id'],'sha256':item['sha256']}]})
        return ProviderCompletion('synthetic',json.dumps(output,ensure_ascii=False),'',(),'stop',ProviderUsage(20,20))

def synthetic_run(store,ws,mid,rid,cancel,**kwargs):
    store.mark_run_running(ws,mid,rid)
    if cancel.wait(.7):
        store.cancel_run(ws,mid,rid); return
    snap=store.get_run_snapshot(ws,mid,rid)
    if not snap.draft:
        draft=store.execute_run_tool(ws,mid,rid,UpdateDefinitionDraftCall(call_id=str(uuid4()),name='update_definition_draft',arguments={'expected_version':0,'expected_sha256':None,'fields':[field('amount')],'relationships':[],'unresolved_items':[]})).output
        base={'why_needed':'影响统计金额与结果可核对性','expected_answer_type':'text','suggested_owner_role':'业务负责人','related_definition_paths':['fields.amount.rule'],'evidence_requested':['明确的业务说明'],'examples_or_options':[],'blocking_impact':'blocking','source_refs':[]}
        store.execute_run_tool(ws,mid,rid,CreateClarificationCall(call_id=str(uuid4()),name='create_clarification',arguments={'draft_version':draft.version,'draft_sha256':draft.sha256,'questions':[{**base,'question':'退款是否从订单金额中扣除？'},{**base,'question':'缺失金额如何处理？','related_definition_paths':['fields.amount.null_handling']}]}))
        store.save_run_final_output(ws,mid,rid,'【合成验收】已建立金额候选定义。有两项业务口径需要你确认；可直接在右侧回答或追问原因。')
    else:
        store.execute_run_tool(ws,mid,rid,FinishRunCall(call_id=str(uuid4()),name='finish_run',arguments={'outcome':'partial','reason':'【合成验收】已按精确批准读取回答，候选仍需核对。','source_refs':[]}))
        store.save_run_final_output(ws,mid,rid,'【合成验收】已读取本次批准并完成这一轮分析。候选结果仍需核对，未发布正式Contract。')

local_settings.external_key_source=lambda:'environment'
agent.get_provider=lambda **kwargs:SyntheticProvider()
agent.run_agent=synthetic_run
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port',type=int,default=8831)
parser.add_argument('--reuse-data-dir',type=Path,help='Reopen only a fixture-marked synthetic database')
parser.add_argument('--empty',action='store_true',help='Exercise first-send local workspace creation')
parser.add_argument('--missing-model',action='store_true',help='Show connection prompt without reading credentials')
parser.add_argument('--settings-delay',type=float,default=0,help='Synthetic settings latency for workspace-switch checks')
args=parser.parse_args()
root=args.reuse_data_dir or Path(tempfile.mkdtemp(prefix='contextox-agent-led-browser-'))
if args.reuse_data_dir:
    marker=root/'fixture.json'
    if not marker.is_file() or json.loads(marker.read_text()).get('synthetic') is not True:
        parser.error('reuse requires this runner’s synthetic fixture marker')
app=create_app(data_dir=root,static_dir=Path(__file__).resolve().parents[1] / 'web' / 'dist',agent_profile='demo-fast')
if args.missing_model:
    local_settings.external_key_source=lambda:None
    local_settings.MacKeychain=type('SyntheticEmptyKeychain',(),{'contains':lambda self:False})
if args.settings_delay:
    import asyncio
    @app.middleware('http')
    async def delayed_settings(request,call_next):
        if request.url.path=='/api/local-settings/deepseek':
            await asyncio.sleep(args.settings_delay)
        return await call_next(request)
store=app.state.workspace_store
ws=None
if not args.empty and not args.reuse_data_dir:
    ws=store.create_workspace('合成验收 · 不连接模型').workspace_id
    for name,data in [('订单.csv','region,amount\n华东,20\n华南,30\n'),('退款.csv','region,refund\n华东,5\n华南,0\n')]:
        store.import_source_revision(ws,name,'text/csv',data.encode())
    store.create_workspace('范围隔离 · 空工作区')
if not args.reuse_data_dir:
    (root/'fixture.json').write_text(json.dumps({'workspace_id':ws,'synthetic':True}))
print('SYNTHETIC_DATA_DIR='+str(root),flush=True)
uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')
