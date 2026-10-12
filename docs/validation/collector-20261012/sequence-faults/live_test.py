import asyncio, gzip, json, os, resource, time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import scripts.record_kalshi_weather_l2_archive as c

ROOT=Path('/data/weather/isolated-tests/ab73d4b-gap-20261012')
PROD=Path('/data/weather/status/kalshi_l2_archive_health.json')
started=time.monotonic(); samples=[]; events=[]; aborted=None; connections=0
original_connect=c.websockets.connect; original_atomic=c.atomic_json
history=gzip.open(ROOT/'bbo_history.jsonl.gz','wt')

def event(kind,**details):
    row={'elapsed_s':round(time.monotonic()-started,3),'kind':kind,**details};events.append(row);print(json.dumps(row),flush=True)

def atomic(path,payload):
    original_atomic(path,payload)
    if path==ROOT/'bbo.json':
        history.write(json.dumps({'elapsed_s':time.monotonic()-started,'payload':payload})+'\n');history.flush()
        if payload['quote_count']==0:event('EMPTY_BBO')
c.atomic_json=atomic

class Socket:
    def __init__(self,ws,index):
        self.ws=ws;self.index=index;self.duplicate_done=False;self.gap_done=False;self.pending=None;self.last_book=None
    def __getattr__(self,k):return getattr(self.ws,k)
    async def recv(self):
        if self.pending is not None:
            raw=self.pending;self.pending=None
        else:raw=await self.ws.recv()
        msg=json.loads(raw)
        if self.index==1 and msg.get('type')=='orderbook_delta':
            if not self.duplicate_done and time.monotonic()-started>=20 and self.last_book is not None:
                self.duplicate_done=True;self.pending=raw;event('INJECTED_DUPLICATE_DELTA')
                return self.last_book
            if not self.gap_done and time.monotonic()-started>=30:
                self.gap_done=True;event('INJECTED_ORDERBOOK_SEQUENCE_GAP',skipped_seq=msg.get('seq'))
                (ROOT/'injected_skipped_message.json').write_text(json.dumps(msg))
                return await self.recv()
            self.last_book=raw
        return raw

@asynccontextmanager
async def connect(*args,**kwargs):
    global connections
    async with original_connect(*args,**kwargs) as ws:
        connections+=1;event('TEST_CONNECTION_OPEN',number=connections)
        yield Socket(ws,connections)
c.websockets.connect=connect

class Writer(c.HourlyWriter):
    def write(self,received,row):
        if row['message_type']=='orderbook_snapshot' and row['generation'] not in first_snapshots:
            first_snapshots.add(row['generation']);event('FIRST_SNAPSHOT',generation=row['generation'])
        super().write(received,row)
first_snapshots=set();c.HourlyWriter=Writer

def read_json(p):
    try:return json.loads(p.read_text())
    except Exception:return {}

def rss_kib():
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith('VmRSS:'):return int(line.split()[1])
    return 0

def cpu_seconds():
    r=resource.getrusage(resource.RUSAGE_SELF);return r.ru_utime+r.ru_stime

async def monitor(task):
    global aborted
    prev_time=time.monotonic();prev_cpu=cpu_seconds();high_cpu=0
    prod_message=read_json(PROD).get('messages',0);last_progress=prev_time
    while not task.done():
        await asyncio.sleep(1)
        now=time.monotonic();cpu=cpu_seconds();pct=100*(cpu-prev_cpu)/(now-prev_time)
        h=read_json(ROOT/'health.json');b=read_json(ROOT/'bbo.json');prod=read_json(PROD)
        row={'elapsed_s':now-started,'cpu_one_core_percent':pct,'rss_kib':rss_kib(),'sample_interval_s':now-prev_time,
             'messages':h.get('messages',0),'quotes':b.get('quote_count',0),'generation':h.get('generation'),
             'sequence_gaps':h.get('sequence_gaps',0),'prod_messages':prod.get('messages',0)}
        samples.append(row);prev_time=now;prev_cpu=cpu
        if prod.get('messages',0)>prod_message:prod_message=prod['messages'];last_progress=now
        high_cpu=high_cpu+1 if pct>90 else 0
        if row['rss_kib']>512*1024 or high_cpu>=10 or now-last_progress>20:
            aborted='RESOURCE_OR_PRODUCTION_PROGRESS_GUARD';event('ABORT',reason=aborted);task.cancel();return

async def main():
    before=read_json(PROD);args=SimpleNamespace(series=','.join(c.ALL_DAILY_TEMPERATURE_SERIES),
      all_weather_catalog=ROOT/'catalog.json',ticker_cache=ROOT/'ticker_cache.json',
      key_id=os.environ.get('KALSHI_API_KEY_ID') or os.environ.get('KALSHI_ACCESS_KEY'),private_key_path=os.environ.get('KALSHI_PRIVATE_KEY_PATH'),
      ws_url=c.WS_URL,rest_timeout=15,refresh_seconds=300,health_interval=1,bbo_interval=2,
      health_output=ROOT/'health.json',bbo_output=ROOT/'bbo.json',raw_dir=ROOT/'raw',flush_every=25,max_backoff=5)
    assert args.key_id and args.private_key_path,'missing credentials'
    task=asyncio.create_task(c.main_async(args));watch=asyncio.create_task(monitor(task))
    try:
        await asyncio.wait_for(task,timeout=70)
    except asyncio.TimeoutError:event('BOUNDED_TEST_COMPLETE')
    except asyncio.CancelledError:event('TEST_CANCELLED')
    finally:
        task.cancel();watch.cancel();await asyncio.gather(task,watch,return_exceptions=True);history.close()
    after=read_json(PROD);h=read_json(ROOT/'health.json');b=read_json(ROOT/'bbo.json')
    result={'sha':'ab73d4b43b2d8f7ce1677599246d1e1b42906d18','duration_s':time.monotonic()-started,
      'aborted':aborted,'connections':connections,'events':events,'samples':samples,'test_health':h,
      'final_quote_count':b.get('quote_count'),'production_before':before,'production_after':after,
      'cpu_seconds':cpu_seconds(),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    original_atomic(ROOT/'result.json',result)
    print('RESULT_FILE='+str(ROOT/'result.json'),flush=True)

asyncio.run(main())
