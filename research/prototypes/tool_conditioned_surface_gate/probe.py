"""Known ball/contact geometry as a pivot feasibility probe, NOT a new method.

Surface z=x^2/(2R), material below it, ball radius r tangent at the valley origin.
No holder, cutting dynamics, finite toolpath, or full machining certification.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


def validate(radius, tool_radius, half_width):
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (radius,tool_radius,half_width)):
        raise ValueError('[INVALID_GEOMETRY] Positive finite radii and half width required.')


def clearance(x, radius, tool_radius):
    """Ball lower surface minus target; negative means material penetration."""
    if abs(x)>tool_radius:
        raise ValueError('[OUTSIDE_BALL] Contact section is defined only inside ball projection.')
    # Rationalized expression avoids subtraction cancellation near x=0.
    lower=x*x/(tool_radius+math.sqrt(max(0.,tool_radius*tool_radius-x*x)))
    return lower-x*x/(2*radius)


def analytic_contact(radius,tool_radius,half_width):
    validate(radius,tool_radius,half_width)
    # Derivative = x*(1/sqrt(r*r-x*x)-1/R).
    x=0. if tool_radius<=radius else min(half_width,math.sqrt(tool_radius*tool_radius-radius*radius))
    minimum=clearance(x,radius,tool_radius)
    return dict(radius_ratio=tool_radius/radius,gouges=tool_radius>radius,
                min_clearance_mm=minimum,witness_x_mm=x)


def sampled_contact(radius,tool_radius,half_width,samples=10001):
    validate(radius,tool_radius,half_width)
    if type(samples) is not int or samples<2:
        raise ValueError('[INVALID_SAMPLES] At least two samples required.')
    limit=min(tool_radius,half_width)
    values=[clearance(limit*i/(samples-1),radius,tool_radius) for i in range(samples)]
    return min(values)


def run(output):
    output=Path(output)
    if output.exists():raise FileExistsError('[FROZEN_PROBE] Preserve previous gate results.')
    output.parent.mkdir(parents=True,exist_ok=True)
    rows=[]
    for scale in (.1,1.,10.):
        radius,half_width=5*scale,4*scale
        for r in (2*scale,5*scale,8*scale):
            exact=analytic_contact(radius,r,half_width)
            sampled=sampled_contact(radius,r,half_width)
            # Every graph normal has positive z; this property is independent of r.
            minimum_normal_z=1/math.sqrt(1+(half_width/radius)**2)
            rows.append(dict(scale=scale,surface_radius_mm=radius,tool_radius_mm=r,half_width_mm=half_width,
                             min_normal_z=minimum_normal_z,normal_only_undercut=False,
                             sampled_min_clearance_mm=sampled,**exact))
    report=dict(rows=rows,completed=True,api_calls=0,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        interpretation='Same surface and normals, different prescribed balls give different contact feasibility. This is classical geometry, not a new theorem, method, or evidence of LLM necessity.',
        scope='Single fixed tangent pose and graph cross-section only; no holder, approach motion, swept-volume simulation, STEP or machining certificate.',
        paper_gate='Not passed: mechanism contrast works, but scientific novelty and model-level benefit remain unverified.')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    report=run(parser.parse_args().output)
    print(json.dumps(report,ensure_ascii=False,indent=2))
