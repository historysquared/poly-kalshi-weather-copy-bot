import gzip,json,hashlib
from pathlib import Path
from decimal import Decimal as D
from collections import Counter
ROOT=Path('/data/weather/isolated-tests/ab73d4b-gap-20261012')
requested={};history_rows=0;history_quotes=0
with gzip.open(ROOT/'bbo_history.jsonl.gz','rt') as fh:
 for line in fh:
  row=json.loads(line);history_rows+=1
  for ticker,q in row['payload']['quotes'].items():
   requested[(ticker,q['sequence'],q['local_receive_timestamp'])]=q;history_quotes+=1
books={};connection=None;seqs={};matched=0;errors=[];types=Counter();snapshots=Counter();gaps=0;duplicates=0;delta_before_snapshot=0

def summary(yes,no):
 yb=sorted(yes.items(),reverse=True);nb=sorted(no.items(),reverse=True)
 ya=sorted((D(1)-p,q) for p,q in no.items());na=sorted((D(1)-p,q) for p,q in yes.items())
 out={}
 for name,levels in [('yes_bid',yb),('no_bid',nb),('yes_ask',ya),('no_ask',na)]:
  out[name]=levels[0][0] if levels else None
  out[name+'_size']=levels[0][1] if levels else None
  out[name+'_depth_top5']=sum((q for p,q in levels[:5]),D(0))
 for side in ['yes','no']:
  bid,ask=out[side+'_bid'],out[side+'_ask']
  out[side+'_spread']=None if bid is None or ask is None else ask-bid
 return out

for path in sorted((ROOT/'raw').rglob('*.gz')):
 with gzip.open(path,'rt') as fh:
  for line in fh:
   r=json.loads(line);types[r['message_type']]+=1
   if r['connection_id']!=connection:connection=r['connection_id'];books={};seqs={}
   sid,seq=r['subscription_id'],r['sequence'];kind=r['message_type'];ticker=r['ticker'];msg=r['raw'].get('msg',{})
   if isinstance(sid,int) and isinstance(seq,int):
    previous=seqs.get(sid)
    if previous is not None and seq<=previous:duplicates+=1;continue
    if previous is not None and seq>previous+1:gaps+=seq-previous-1;books={};continue
    seqs[sid]=seq
   if kind=='orderbook_snapshot':
    snapshots[r['generation']]+=1
    books[ticker]=({D(p):D(q) for p,q in msg.get('yes_dollars_fp',[]) if D(q)>0},
                   {D(p):D(q) for p,q in msg.get('no_dollars_fp',[]) if D(q)>0})
   elif kind=='orderbook_delta':
    if ticker not in books:delta_before_snapshot+=1;continue
    side=msg['side'];book=books[ticker][0 if side=='yes' else 1]
    price=D(msg['price_dollars']);size=book.get(price,D(0))+D(msg['delta_fp'])
    if size<=0:book.pop(price,None)
    else:book[price]=size
   else:continue
   key=(ticker,seq,r['local_receive_timestamp'])
   if key in requested:
    expected=requested.pop(key);got=summary(*books[ticker])
    for name,value in got.items():
     q=expected.get(name);q=None if q is None else D(q)
     if q!=value:errors.append({'ticker':ticker,'seq':seq,'field':name,'expected':str(q),'replay':str(value)})
    matched+=1
result=json.loads((ROOT/'result.json').read_text());samples=result['samples'];cpu=[r['cpu_one_core_percent'] for r in samples]
first=[e for e in result['events'] if e['kind']=='FIRST_SNAPSHOT'];forced=next((e['elapsed_s'] for e in result['events'] if e['kind']=='INJECTED_ORDERBOOK_SEQUENCE_GAP'),None)
recovered=next((e['elapsed_s'] for e in first if e['generation']==1),None)
files=sorted(ROOT.rglob('*'));raw_bytes=sum(p.stat().st_size for p in (ROOT/'raw').rglob('*.gz'))
output={'sha':result['sha'],'duration_s':result['duration_s'],'aborted':result['aborted'],'connections':result['connections'],
 'messages':sum(types.values()),'message_types':dict(types),'snapshots_by_generation':dict(snapshots),
 'sequence_gaps':gaps,'duplicates':duplicates,'delta_before_snapshot':delta_before_snapshot,
 'bbo_history_writes':history_rows,'bbo_quote_observations':history_quotes,'unique_quote_states_matched':matched,
 'unmatched_quote_states':len(requested),'comparison_errors':errors[:20],'comparison_error_count':len(errors),
 'recovery_first_snapshot_seconds':None if forced is None or recovered is None else recovered-forced,
 'avg_cpu_one_core_percent':result['cpu_seconds']/result['duration_s']*100,'max_sample_cpu_percent':max(cpu),
 'peak_rss_mib':result['peak_rss_kib']/1024,'max_monitor_interval_s':max(r['sample_interval_s'] for r in samples),
 'final_quote_count':result['final_quote_count'],'series_count':result['test_health']['series_count'],
 'test_reconnects':result['test_health']['reconnects'],'test_last_error':result['test_health']['last_error'],
 'raw_compressed_mib':raw_bytes/(1024*1024),
 'production_message_increment':result['production_after']['messages']-result['production_before']['messages'],
 'production_connection_id_unchanged':result['production_after']['connection_id']==result['production_before']['connection_id'],
 'production_gap_increment':result['production_after']['sequence_gaps']-result['production_before']['sequence_gaps'],
 'production_reconnect_increment':result['production_after']['reconnects']-result['production_before']['reconnects'],
 'production_last_error':result['production_after']['last_error']}
(ROOT/'verification.json').write_text(json.dumps(output,indent=2)+'\n');print(json.dumps(output,indent=2))
