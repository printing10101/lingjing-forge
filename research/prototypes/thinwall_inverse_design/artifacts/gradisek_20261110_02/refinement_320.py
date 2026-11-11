from pathlib import Path
import hashlib,json,time
from dataclasses import replace
import numpy as np
from prototypes.thinwall_inverse_design.coupled_milling import CoupledModes,Milling,coupled_radius
from prototypes.thinwall_inverse_design.benchmark import confusion
run=Path(__file__).resolve().parent
source=run/'source'
config=json.loads((source/'benchmark_config.json').read_text(encoding='utf-8'))
previous=json.loads((run/'results.json').read_text(encoding='utf-8'))
current=Path('prototypes/thinwall_inverse_design/coupled_milling.py')
assert hashlib.sha256(current.read_bytes()).hexdigest()==hashlib.sha256((source/'coupled_milling.py').read_bytes()).hexdigest()
mass=np.array(config['mass_kg']); stiffness=np.array(config['stiffness_n_m'])
modes=CoupledModes(np.sqrt(stiffness/mass),np.array(config['damping_n_s_m'])/(2*np.sqrt(mass*stiffness)),np.diag(1/np.sqrt(mass)))
tool=Milling(**config['milling'])
ratios={'D':1.0,'0.5D':0.5,'0.1D':0.1,'0.05D':0.05}
rows=[]; started=time.monotonic()
for i,row in enumerate(previous['rows']):
    rho=coupled_radius(modes,row['rpm'],row['depth_mm']/1000,320,replace(tool,immersion=ratios[row['ae_label']]))
    rows.append({**row,'rho_320':rho,'stable_320':bool(rho<1)})
    if (i+1)%100==0: print('refine320',i+1,'elapsed',round(time.monotonic()-started,1),flush=True)
    if time.monotonic()-started>240: raise RuntimeError('refinement budget reached; no final metrics')
result={'purpose':'Numerical refinement after80/160 discrepancies, no parameter/label fitting','all_rows':confusion(rows,'stable_320'),'by_immersion':{key:confusion([r for r in rows if r['ae_label']==key],'stable_320') for key in ratios},'changed_160_to_320':sum(r['stable_160']!=r['stable_320'] for r in rows),'rows':rows,'elapsed_seconds':time.monotonic()-started,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
(run/'refinement_320.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False),flush=True)
