"""Gold expectations ONLY for evaluation; application never imports this module."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from carebridge.domain import GROUPS, missing, positive_int
ROOT=Path(__file__).resolve().parents[1]
NAMES=['체질량지수(BMI)','수축기혈압','이완기혈압','공복혈당','총콜레스테롤','AST','ALT']
VALUES=[['22.4','118','76','92','182','22','19'],['26.1','134','84','110','223','29','44'],['23.0','121',None,None,'194','31',None]]
REFS=['18.5-24.9','90-119','60-79','70-99','0-199','0-40','0-40']
UNITS=['kg/m²','mmHg','mmHg','mg/dL','mg/dL','U/L','U/L']
MEDS={4:[('가상시약-A정','1정',1,3,'아침 식후 30분'),('가상시약-B캡슐','1캡슐',2,3,'아침, 저녁 식후'),('가상시약-C정','1정',1,3,'저녁 식사와 함께')],5:[('가상시약-D정','2정',1,5,'취침 전'),('가상시약-E산','1포',2,5,'식사와 관계없이')],6:[('가상시약-F정','1정',None,7,None),('가상시약-G캡슐','1캡슐',1,None,'아침 식후')]}
report=[]
for n in range(1,7):
    path=ROOT/f'.local/live/sample{n}.json'
    checks=[]
    def check(label,ok):checks.append({'check':label,'pass':bool(ok)})
    if not path.exists():report.append({'sample':n,'error':'not run'});continue
    result=json.loads(path.read_text());data=result['data'];kind='health_checkup_result' if n<=3 else 'medication_guidance_record'
    check('classification',result['kind']==kind)
    rows=data[GROUPS[kind]]
    if n<=3:
        check('row count',len(rows)==7)
        byname={r['test_item_name']:r for r in rows}
        for i,name in enumerate(NAMES):
            r=byname.get(name,{})
            check(name+' value association',missing(r.get('result_value','')) if VALUES[n-1][i] is None else r.get('result_value')==VALUES[n-1][i])
            check(name+' reference',r.get('reference_range')==REFS[i])
            if VALUES[n-1][i] is not None:check(name+' exact unit',r.get('result_unit')==UNITS[i])
            check(name+' evidence exists',bool(r.get('source_excerpt')))
    else:
        check('row count',len(rows)==len(MEDS[n]));byname={r['medication_name']:r for r in rows}
        for name,dose,count,days,timing in MEDS[n]:
            r=byname.get(name,{})
            check(name+' dose',r.get('single_dose')==dose)
            check(name+' frequency',missing(r.get('times_per_day','')) if count is None else positive_int(r.get('times_per_day',''),r'(?:회)?')==count)
            check(name+' duration',missing(r.get('days_of_administration','')) if days is None else positive_int(r.get('days_of_administration',''),r'(?:일)?')==days)
            check(name+' timing',missing(r.get('administration_timing','')) if timing is None else r.get('administration_timing')==timing)
            check(name+' evidence exists',bool(r.get('source_excerpt')))
    report.append({'sample':n,'checks':len(checks),'passed':sum(x['pass'] for x in checks),'failures':[x['check'] for x in checks if not x['pass']]})
(ROOT/'.local/live/evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
