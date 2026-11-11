"""Post-audit fixed-parameter diagnostic; all results retained, no fitting."""
from pathlib import Path
from dataclasses import replace
import csv,hashlib,json,time
import numpy as np
from prototypes.thinwall_inverse_design.coupled_milling import CoupledModes,Milling,coupled_radius
from prototypes.thinwall_inverse_design.benchmark import confusion

def main():
    here=Path(__file__).resolve().parent
    repo=here.parents[4]
    for name in ('coupled_milling.py','mechanics.py'):
        assert (here/name).read_bytes()==(here.parents[1]/name).read_bytes(), 'Source drift'
    plan=json.loads((here/'plan.json').read_text(encoding='utf-8')); config=plan['base_config']
    reference=repo/config['source_csv']
    assert hashlib.sha256(reference.read_bytes()).hexdigest()==plan['reference_sha256']
    assert not (here/'results.json').exists(), 'Do not overwrite a completed diagnostic'
    masses=np.array(config['mass_kg']);k=np.array(config['stiffness_n_m']);c=np.array(config['damping_n_s_m'])
    modes=CoupledModes(np.sqrt(k/masses),c/(2*np.sqrt(masses*k)),np.diag(1/np.sqrt(masses)))
    rows=[];start=time.monotonic()
    for source in csv.DictReader(reference.open(encoding='utf-8-sig')):
        row={'original_row':int(source['original_row']),'ae_label':source['ae_label'],'rpm':float(source['n_rpm']),'depth_mm':float(source['ap_mm']),'stable_true':source['result']=='stable'}
        for helix in plan['variants_helix_deg']:
            milling=replace(Milling(**config['milling']),helix_deg=helix,immersion=float(source['ae_mm'])/8)
            for steps in plan['steps']:
                key=f'h{helix}_s{steps}'
                row['rho_'+key]=coupled_radius(modes,row['rpm'],row['depth_mm']/1000,steps,milling,config['delay_rule'])
                row['stable_'+key]=row['rho_'+key]<1
        rows.append(row)
    summary={f'h{h}_s{s}':confusion(rows,f'stable_h{h}_s{s}') for h in plan['variants_helix_deg'] for s in plan['steps']}
    baseline_errors=[r['original_row'] for r in rows if r['stable_h45_s320']!=r['stable_true']]
    unresolved=[r['original_row'] for r in rows if r['original_row'] in baseline_errors and r['stable_h0_s320']!=r['stable_true']]
    result={'summary':summary,'baseline_error_rows':baseline_errors,'baseline_errors_remaining_without_helix':unresolved,'variant_classification_changes_320':sum(r['stable_h0_s320']!=r['stable_h45_s320'] for r in rows),'step_label_changes':{h:sum(r[f'stable_h{h}_s160']!=r[f'stable_h{h}_s320'] for r in rows) for h in plan['variants_helix_deg']},'elapsed_seconds':time.monotonic()-start,'rows':rows,'population_accuracy_supported':False,'parameters_fitted':False}
    (here/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}))
if __name__=='__main__':main()
