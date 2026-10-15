"""Explicit live integration run; costs API credits. Output never includes credentials."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from carebridge.client import AgentClient, AnalysisError, ROOT
from carebridge.domain import parse_job, GROUPS

def main():
    out=ROOT/'.local/live';out.mkdir(parents=True,exist_ok=True)
    for p in sorted((ROOT/'samples').glob('*.pdf')):
        try:
            client=AgentClient()
            job=client.analyze(p.read_bytes())
            (out/(p.stem+'-raw.json')).write_text(json.dumps(job,ensure_ascii=False,indent=2))
            kind,data=parse_job(job)
            (out/(p.stem+'.json')).write_text(json.dumps({'kind':kind,'data':data,'cleanup_warning':client.cleanup_warning},ensure_ascii=False,indent=2))
            print(p.name,kind,len(data[GROUPS[kind]]),'cleanup_warning=',client.cleanup_warning,flush=True)
        except AnalysisError as e:
            print(p.name,'ERROR',str(e),flush=True)
if __name__=='__main__': main()
