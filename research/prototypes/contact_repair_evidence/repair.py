"""Candidate search with hard locks; numerical search never proves global infeasibility."""
from dataclasses import dataclass
from fractions import Fraction as F
from math import comb
import numpy as np
from scipy.interpolate import BSpline
from scipy.optimize import linprog
from verification import verify_ball, rational


def initial_curve(scale=1.0):
    """Exact quartic graph z=.1*x²-.003*x⁴, geometrically scaled in mm."""
    if not np.isfinite(scale) or scale<=0:
        raise ValueError('[SCALE] Positive scale required. 建议操作：检查尺度。')
    # x=8u-4, expanded using rational polynomial arithmetic.
    power=[F(104,125),F(-32,125),F(-1504,125),F(3072,125),F(-1536,125)]
    controls=[sum((power[j]*F(comb(i,j),comb(5,j)) for j in range(min(i,4)+1)),F(0))
              for i in range(6)]
    return BSpline(np.array([-4.]*6+[4.]*6)*scale,
                   np.array([float(c)*scale for c in controls]),5)


@dataclass(frozen=True)
class Request:
    radius: float = 8.0
    displacement_budget: float = .6
    lock_order: int = 2
    protected_x: tuple[float,...] = ()
    max_controls: int = 30
    max_solves: int = 40
    tolerance: float = 1e-6

    def __post_init__(self):
        numbers=(self.radius,self.displacement_budget,self.tolerance,*self.protected_x)
        if (not np.isfinite(numbers).all() or self.radius<=0 or self.displacement_budget<0 or
            self.tolerance<=0 or self.lock_order not in (0,1,2) or
            type(self.max_controls) is not int or self.max_controls<6 or
            type(self.max_solves) is not int or self.max_solves<1):
            raise ValueError('[REQUEST] Invalid geometry, lock or budget. 建议操作：检查请求参数。')


def ball_height(x,r):
    return x*x/(r+np.sqrt(np.maximum(0.,r*r-x*x)))


def equality_system(curve,request):
    count=len(curve.c); basis=BSpline(curve.t,np.eye(count),curve.k)
    a,b=[],[]
    for x in (curve.t[0],curve.t[-1]):
        for order in range(request.lock_order+1):
            row=basis(x,nu=order); norm=np.linalg.norm(row)
            a.append(row/norm);b.append(curve(x,nu=order)/norm)
    for x,order in [(0.,0),(0.,1)]+[(x,0) for x in request.protected_x]:
        row=basis(x,nu=order);norm=np.linalg.norm(row)
        a.append(row/norm);b.append(curve(x,nu=order)/norm)
    return np.asarray(a),np.asarray(b)


def solve_samples(original,request,points):
    """Phase I minimizes contact slack, phase II control-net L1 change."""
    n=len(original.c);basis=BSpline(original.t,np.eye(n),original.k)
    rows=basis(points);height=ball_height(points,request.radius)
    aeq,beq=equality_system(original,request)
    # Control bounds imply a continuous displacement bound by partition of unity.
    bounds=[(v-request.displacement_budget,v+request.displacement_budget) for v in original.c]
    phase=linprog(np.r_[np.zeros(n),1.],A_ub=np.c_[rows,-np.ones(len(points))],
        b_ub=height+request.tolerance/4,A_eq=np.c_[aeq,np.zeros(len(beq))],b_eq=beq,
        bounds=bounds+[(0,None)],method='highs')
    if not phase.success:
        return None,dict(reason='solver_failure',message=phase.message)
    slack=float(phase.x[-1])
    info=dict(slack_mm=slack,dual_weights=np.abs(phase.ineqlin.marginals).tolist())
    if slack>request.tolerance/8:
        return None,info
    # Deliberately keep the independent verification tolerance larger than search slack.
    matrix=np.vstack([np.c_[rows,np.zeros((len(points),n))],
                      np.c_[np.eye(n),-np.eye(n)],np.c_[-np.eye(n),-np.eye(n)]])
    rhs=np.r_[height+request.tolerance/2,original.c,-original.c]
    fit=linprog(np.r_[np.zeros(n),np.ones(n)],A_ub=matrix,b_ub=rhs,
        A_eq=np.c_[aeq,np.zeros((len(beq),n))],b_eq=beq,
        bounds=bounds+[(0,None)]*n,method='highs')
    if not fit.success:
        return None,{**info,'reason':'secondary_solver_failure'}
    return BSpline(original.t,fit.x[:n],original.k),info


def choose_knots(curve,points,info,policy):
    breaks=np.unique(curve.t)
    midpoints=(breaks[:-1]+breaks[1:])/2
    if policy=='uniform':
        return midpoints.tolist()
    weights=np.array(info.get('dual_weights',np.ones(len(points))))
    score=[float(weights[(points>=a)&(points<=b)].sum()) for a,b in zip(breaks[:-1],breaks[1:])]
    # Deterministic tie-break by interval width then left endpoint; heuristic, not Farkas pricing.
    index=max(range(len(score)),key=lambda i:(score[i],breaks[i+1]-breaks[i],-i))
    return [float(midpoints[index])]


def repair(original,request,policy='targeted'):
    if policy not in ('targeted','uniform','fixed'):
        raise ValueError('[POLICY] Unknown refinement policy. 建议操作：选择合法策略。')
    if original.t[0]<-request.radius or original.t[-1]>request.radius or not original.t[0]<=0<=original.t[-1]:
        raise ValueError('[DOMAIN] Curve must include zero and lie inside ball projection. 建议操作：检查域。')
    if any(not original.t[0]<=x<=original.t[-1] for x in request.protected_x):
        raise ValueError('[PROTECTED_POINT] Lock outside curve domain. 建议操作：检查锁定点。')
    for x in request.protected_x:
        gap=float(ball_height(x,request.radius)-original(x))
        if gap < -request.tolerance:
            return dict(status='protected_point_conflict',witness_x=x,gap_mm=gap,
                        scope='fixed_pose_and_exact_point_lock',history=[]),None
    # Any admissible vertical edit is at least z0(x)-B pointwise. If even this
    # relaxed lower envelope intrudes, NO representation can repair it within B.
    lower=[rational(c)-rational(request.displacement_budget) for c in original.c]
    obstruction=verify_ball(original.t,lower,original.k,request.radius,request.tolerance)
    if obstruction.status=='fail':
        return dict(status='displacement_budget_conflict',history=[],
                    certificate=vars(obstruction),
                    scope='all_vertical_graph_edits_with_global_Linf_budget_at_fixed_ball_pose'),None
    curve=original
    points=np.unique(np.r_[np.linspace(curve.t[0],curve.t[-1],9),request.protected_x])
    history=[]
    for iteration in range(request.max_solves):
        candidate,info=solve_samples(curve,request,points)
        row=dict(iteration=iteration,controls=len(curve.c),samples=len(points),**info)
        if candidate is not None:
            check=verify_ball(candidate.t,candidate.c,candidate.k,request.radius,request.tolerance)
            row['verification']=vars(check)
            # Independent design equality and control-bound checks; never trust optimizer success alone.
            a,b=equality_system(curve,request)
            row['lock_residual']=float(np.max(np.abs(a@candidate.c-b)))
            row['control_change_bound_mm']=float(np.max(np.abs(candidate.c-curve.c)))
            if check.status=='pass' and row['lock_residual']<1e-7 and row['control_change_bound_mm']<=request.displacement_budget+1e-9:
                history.append(row)
                return dict(status='verified',history=history),candidate
            if check.status=='fail' and check.witness_x not in points:
                history.append(row);points=np.sort(np.r_[points,check.witness_x]);continue
        history.append(row)
        if policy=='fixed':
            return dict(status='representation_unresolved',history=history),None
        knots=choose_knots(curve,points,info,policy)
        if len(curve.c)+len(knots)>request.max_controls:
            return dict(status='budget_unknown',history=history),None
        for knot in knots:
            curve=curve.insert_knot(knot)
    return dict(status='budget_unknown',history=history),None
