"""Evidence-gated refinement. Search failures are never global impossibility."""
from fractions import Fraction as F
from math import isfinite
from envelope_solver import Section, solve_fixed, replay_solution
from verification import verify_ball, rational, contact_budget_bounds


def preflight(section, request):
    """Representation-independent witnesses for two necessary conditions."""
    from envelope_solver import basis_row, dot, sqrt_lower
    radius, eps = rational(request.radius), rational(request.tolerance)
    if section.knots[0] < -radius or section.knots[-1] > radius:
        raise ValueError('[BALL_DOMAIN] Curve outside ball projection. 建议操作：统一定义域。')
    for x in map(rational, (section.knots[0], section.knots[-1], 0, *request.protected_x)):
        value = dot(basis_row(section.knots, section.degree, x), section.controls)
        upper = radius-sqrt_lower(radius*radius-x*x)+eps
        if value > upper:
            return dict(status='protected_point_conflict', witness_rational=str(x))
    # z0-B is below every allowed edit at the same x. Its violation rules out
    # all such graph edits, regardless of basis size or search strategy.
    envelope = [c-rational(request.displacement_budget) for c in section.controls]
    check = verify_ball(section.knots, envelope, section.degree, radius, eps)
    if check.status == 'fail':
        return dict(status='displacement_budget_conflict', contact=vars(check))
    return None


def repair_quality(section, request, policy='evidence', quality_gap=1e-4,
                   solve_gap=1e-5, max_basis_solves=36):
    """Compare uniform, dual-local, and verified one-knot lookahead refinement.

    All policies use the SAME actual geometric minimax solver and stopping
    criteria. Evidence lookahead tests all span midpoints, counts every trial,
    and commits only a continuously certified, quality-improving candidate.
    If no feasible candidate exists yet, uniform refinement restores freedom.
    This is an exploratory policy, not a claim of novel LP/refinement theory.
    """
    if (policy not in ('uniform', 'dual_local', 'evidence') or not isfinite(quality_gap) or quality_gap <= 0
            or not isfinite(solve_gap) or solve_gap <= 0
            or type(max_basis_solves) is not int or max_basis_solves < 1):
        raise ValueError('[POLICY_CONFIG] Invalid policy or budget. 建议操作：检查精化策略。')
    conflict = preflight(section, request)
    if conflict:
        return dict(**conflict, basis_solves=0, history=[]), None
    # Necessary contact-only optimum, independent of design locks and basis.
    ceiling = max(F(1), max(section.controls)-min(section.controls)+2*rational(request.radius))
    necessary = contact_budget_bounds(section.knots, section.controls, section.degree,
                                     request.radius, ceiling, request.tolerance, accuracy=solve_gap/8)
    # An UNKNOWN older helper returns floats only. Do not promote a rounded
    # display value into an exact lower certificate; zero remains valid.
    global_lower = F(0)
    if 'lower_rational' in necessary:
        global_lower = F(necessary['lower_rational'])
    current, incumbent, best_record = section, None, None
    solves, history = 0, []

    def evaluate(basis, role):
        nonlocal solves, incumbent, best_record
        record, candidate = solve_fixed(basis, request, gap=solve_gap)
        solves += 1
        certified = (record['status'] == 'verified' and candidate is not None
                     and replay_solution(basis, candidate, request, record, solve_gap))
        history.append(dict(role=role, controls=len(basis.controls), knots=list(map(str, basis.knots)),
                            status=record['status'], upper_mm=record.get('upper_mm'),
                            lower_mm=record.get('lower_mm'), iterations=record.get('iterations'),
                            lp_calls=record.get('lp_calls', 0),
                            committed=False, certified=certified))
        if certified and (best_record is None or F(record['norm']['upper_rational'])
                          < F(best_record['norm']['upper_rational'])):
            incumbent, best_record = candidate, record
        return record, candidate, certified

    record, candidate, certified = evaluate(current, 'initial')
    history[-1]['committed'] = True
    while True:
        if best_record and F(best_record['norm']['upper_rational'])-global_lower <= rational(quality_gap):
            return dict(status='verified_quality', basis_solves=solves, history=history,
                        necessary_contact=necessary, best=best_record,
                        controls=len(incumbent.controls),
                        global_gap_upper_mm=float(F(best_record['norm']['upper_rational'])-global_lower)), incumbent
        breaks = sorted(set(current.knots))
        midpoints = [(a+b)/2 for a, b in zip(breaks[:-1], breaks[1:])]
        if solves >= max_basis_solves or len(current.controls) >= request.max_controls:
            break
        if policy == 'evidence' and certified:
            trials = []
            # Do not silently hide lookahead work or exceed its solve budget.
            for midpoint in midpoints:
                if solves >= max_basis_solves:
                    break
                trial_basis = current.refine([midpoint])
                trial_record, trial_candidate, trial_ok = evaluate(trial_basis, 'lookahead')
                if trial_ok:
                    trials.append((F(trial_record['norm']['upper_rational']), len(history)-1,
                                   trial_basis, trial_record, trial_candidate))
            if not trials:
                break
            upper, index, proposed, proposed_record, proposed_candidate = min(trials, key=lambda row: row[0])
            if upper >= F(record['norm']['upper_rational']):
                break
            current, record, candidate = proposed, proposed_record, proposed_candidate
            certified = True
            history[index]['committed'] = True
            continue
        if policy == 'dual_local' and record.get('dual'):
            dual = record['dual']
            points = list(map(F, dual['points']))
            weights = dict(zip(dual['support'], map(F, dual['weights'])))
            scores = [sum((weights.get(3*i+2, F(0)) for i, x in enumerate(points)
                           if left <= x <= right), F(0)) for left, right in zip(breaks[:-1], breaks[1:])]
            index = max(range(len(scores)), key=lambda i: (scores[i], breaks[i+1]-breaks[i], -i))
            positions = [midpoints[index]]
        else:
            positions = midpoints
        if len(current.controls)+len(positions) > request.max_controls:
            break
        current = current.refine(positions)
        record, candidate, certified = evaluate(current, 'refinement')
        history[-1]['committed'] = True
    status = 'verified_budget_only' if incumbent is not None else 'unresolved'
    return dict(status=status, basis_solves=solves, history=history,
                necessary_contact=necessary, best=best_record,
                controls=len(incumbent.controls) if incumbent else None), incumbent
