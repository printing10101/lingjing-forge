"""Read frozen evidence; prepare report tables and conventional scientific plots."""
from pathlib import Path
import hashlib
import json
import sys
from fractions import Fraction as F

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import numpy as np
from scipy.interpolate import BSpline

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
SOURCE = ROOT/'research/prototypes/contact_repair_evidence'
RUN = SOURCE/'artifacts/envelope_20261109_01'
rows = json.loads((RUN/'results.json').read_text(encoding='utf-8'))
manifest = json.loads((RUN/'manifest.json').read_text(encoding='utf-8'))
assert all(hashlib.sha256((SOURCE/name).read_bytes()).hexdigest() == digest
           for name, digest in manifest['source_sha256'].items())
probe = json.loads((SOURCE/'artifacts/coupling_probe_20261109.json').read_text())
summary = json.loads((RUN/'summary.json').read_text())
font = FontProperties(fname='C:/Windows/Fonts/simsun.ttc')
plt.rcParams.update({'font.family':'DejaVu Sans', 'axes.unicode_minus':False,
                     'axes.spines.top':False, 'axes.spines.right':False, 'font.size':10})

def curve(data):
    from fractions import Fraction
    return BSpline(np.array([float(Fraction(v)) for v in data['knots']]),
                   np.array([float(Fraction(v)) for v in data['controls']]), data['degree'])

def get(case, policy):
    return next(r for r in rows if r['case_id'] == case and r['policy'] == policy)

representative = []
for policy in manifest['policies']:
    row = get(3, policy)
    representative.append(dict(policy=policy, status=row['status'], controls=row.get('controls'),
        change_upper_mm=row.get('geometry_bound', {}).get('upper_mm'), lp_calls=row['lp_calls'],
        elapsed_s=row['elapsed_s'], global_gap_upper_mm=row.get('global_gap_upper_mm')))
quality_counts = {policy:sum(r.get('global_gap_upper_mm', 1.) <= 1e-4 for r in rows if r['policy'] == policy)
                  for policy in manifest['policies']}
costs = {policy:dict(lp_calls=sum(r['lp_calls'] for r in rows if r['policy'] == policy),
                    elapsed_s=sum(r['elapsed_s'] for r in rows if r['policy'] == policy))
         for policy in manifest['policies']}
data = dict(summary=summary, representative=representative, quality_counts=quality_counts,
            costs=costs, coupling=probe['conclusion'],
            source_hashes_match=True, representative_contact=get(3, 'uniform')['necessary_contact'])
probe_rows = {tuple(row['insertions']):row['record'] for row in probe['records']}
interaction_targets = []
base_record = probe_rows[()]
for selected, pair in probe_rows.items():
    if len(selected) != 2:continue
    left, right = [probe_rows[(k,)] for k in selected]
    # S_ij = T_i + T_j - T_ij - T_0: ordinary discrete interaction,
    # now enclosed using the independently replayed geometric objective bounds.
    lower = (F(left['dual']['lower_rational'])+F(right['dual']['lower_rational'])
             -F(pair['norm']['upper_rational'])-F(base_record['norm']['upper_rational']))
    upper = (F(left['norm']['upper_rational'])+F(right['norm']['upper_rational'])
             -F(pair['dual']['lower_rational'])-F(base_record['dual']['lower_rational']))
    assert lower <= upper
    interaction_targets.append(dict(insertions=list(selected), synergy_lower_mm=float(lower),
        synergy_upper_mm=float(upper), lower_rational=str(lower), upper_rational=str(upper),
        positive_synergy_certified=lower > 0))
data['interaction_targets'] = interaction_targets
(HERE/'interaction_targets.json').write_text(json.dumps(dict(phase='post_hoc_targets_not_trained_model',
    input_sha256=hashlib.sha256((SOURCE/'artifacts/coupling_probe_20261109.json').read_bytes()).hexdigest(),
    interpretation='Conventional discrete interaction scores; no new-mathematics or generalization claim.',
    targets=interaction_targets), indent=2), encoding='utf-8')
(HERE/'analysis.json').write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

x = np.linspace(-4, 4, 2001)
proofs = {p:json.loads((RUN/f'proof_3_{p}.json').read_text())
          for p in ('legacy_uniform', 'legacy_targeted', 'uniform')}
original = curve(proofs['uniform']['original'])
fig, axes = plt.subplots(2, 1, figsize=(7.2, 5.7), sharex=True)
axes[0].plot(x, original(x), color='#333333', label='Original CAD section')
axes[0].plot(x, 8-np.sqrt(64-x*x), '--', color='#B34C3F', label='Fixed ball lower boundary')
axes[0].plot(x, curve(proofs['uniform']['candidate'])(x), color='#1C668E', label='Verified minimax repair')
axes[0].set_ylabel('z (mm)'); axes[0].legend(frameon=False, fontsize=8)
for policy, color, label in [('legacy_uniform','#929292','Old uniform / control-net L1'),
    ('legacy_targeted','#B77C35','Old targeted / control-net L1'),
    ('uniform','#1C668E','New uniform / geometric minimax')]:
    axes[1].plot(x, np.abs(curve(proofs[policy]['candidate'])(x)-original(x)), color=color, label=label)
axes[1].axhline(get(3,'uniform')['necessary_contact']['lower_mm'], ls='--', color='#B34C3F',
                label='Necessary contact lower bound')
axes[1].set_xlabel('x (mm)'); axes[1].set_ylabel('Absolute vertical change (mm)')
axes[1].legend(frameon=True, facecolor='white', edgecolor='none', framealpha=.97, fontsize=8)
for ax in axes:ax.grid(alpha=.18)
fig.tight_layout(); fig.savefig(HERE/'geometry_comparison.png', dpi=210)
fig.savefig(HERE/'geometry_comparison.svg'); plt.close(fig)

selected = [row for row in probe['records'] if len(row['insertions']) <= 1
            or row['insertions'] in ([-1,1], [-3,3])]
lower = np.array([r['record']['lower_mm'] for r in selected])
upper = np.array([r['record']['upper_mm'] for r in selected])
labels = ['Base']+[(str(r['insertions'][0]) if len(r['insertions']) == 1
                   else '('+','.join(map(str, r['insertions']))+')') for r in selected[1:]]
fig, ax = plt.subplots(figsize=(7.2,3.5))
positions=np.arange(len(selected))
ax.errorbar(positions,(lower+upper)/2,yerr=(upper-lower)/2,fmt='o',capsize=5,color='#1C668E')
ax.set_xticks(positions,labels); ax.set_xlabel('Inserted midpoint positions (mm)')
ax.set_ylabel('Certified minimax change interval (mm)')
ax.grid(axis='y',alpha=.18)
fig.tight_layout();fig.savefig(HERE/'coupling_intervals.png',dpi=210)
fig.savefig(HERE/'coupling_intervals.svg');plt.close(fig)
print(json.dumps(data, indent=2, ensure_ascii=False))
