"""Real image integration: rendered virtual sample4, never a fabricated result."""
import io
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from carebridge.client import AgentClient, ROOT
from carebridge.domain import parse_job
def main():
    source=ROOT/'tmp/pdfs/sample4.png'
    for fmt in ('PNG','JPEG'):
        buf=io.BytesIO()
        with Image.open(source) as im:im.convert('RGB').save(buf,format=fmt)
        client=AgentClient();job=client.analyze(buf.getvalue());kind,data=parse_job(job)
        (ROOT/f'.local/live/photo-{fmt.lower()}.json').write_text(json.dumps({'kind':kind,'data':data,'job_id':job['id']},ensure_ascii=False,indent=2))
        print(fmt,kind,[(r['medication_name'],r['days_of_administration']) for r in data['medication_instructions']],flush=True)

if __name__=='__main__':
    main()
