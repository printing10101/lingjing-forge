"""Exact rational Bernstein checks of a fixed ball versus a graph section.

The polynomial defined by the supplied binary floats is checked exactly.
No claim is made about unmodelled 3D geometry, holders or moving tools.
"""
from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction as F
from math import comb, isfinite
import heapq


def rational(value):
    if isinstance(value, F):
        return value
    if not isfinite(float(value)):
        raise ValueError('[NONFINITE] Finite geometry is required. 建议操作：检查输入数值。')
    return F(float(value))


def elevate(coefficients, degree):
    coefficients = list(coefficients)
    if degree < len(coefficients)-1:
        raise ValueError('[DEGREE] Elevation cannot reduce degree. 建议操作：增大目标次数。')
    while len(coefficients) <= degree:
        n = len(coefficients)-1
        coefficients = [coefficients[0]] + [
            F(i, n+1)*coefficients[i-1] + (1-F(i, n+1))*coefficients[i]
            for i in range(1,n+1)] + [coefficients[-1]]
    return coefficients


def multiply(left, right):
    m,n=len(left)-1,len(right)-1
    return [sum((left[i]*right[k-i]*F(comb(m,i)*comb(n,k-i),comb(m+n,k))
                 for i in range(max(0,k-n),min(m,k)+1)),F(0))
            for k in range(m+n+1)]


def subdivide(coefficients):
    rows=[list(coefficients)]
    while len(rows[-1])>1:
        rows.append([(a+b)/2 for a,b in zip(rows[-1][:-1],rows[-1][1:])])
    return [row[0] for row in rows], [row[-1] for row in rows[::-1]]


def value_at_half(coefficients):
    return subdivide(coefficients)[0][-1]


def insert_exact(knots, controls, degree, position):
    """Boehm knot insertion with rational operations, preserving the curve."""
    knots,controls=list(knots),list(controls)
    k=bisect_right(knots,position)-1
    multiplicity=knots.count(position)
    if not knots[degree]<position<knots[-degree-1] or multiplicity>=degree:
        raise ValueError('[INSERTION] Internal knot multiplicity must be below degree. 建议操作：选择合法节点。')
    updated=[None]*(len(controls)+1)
    for i in range(k-degree+1):
        updated[i]=controls[i]
    for i in range(k-multiplicity,len(controls)):
        updated[i+1]=controls[i]
    for i in range(k-degree+1,k-multiplicity+1):
        alpha=(position-knots[i])/(knots[i+degree]-knots[i])
        updated[i]=alpha*controls[i]+(1-alpha)*controls[i-1]
    return knots[:k+1]+[position]+knots[k+1:],updated


def bezier_spans(knots, controls, degree):
    knots,controls=list(map(rational,knots)),list(map(rational,controls))
    if (type(degree) is not int or not 1<=degree<=10 or
            len(knots)!=len(controls)+degree+1 or
            any(a>b for a,b in zip(knots[:-1],knots[1:])) or
            len(controls)<degree+1 or not knots[degree]<knots[-degree-1] or
            len(set(knots[:degree+1]))!=1 or len(set(knots[-degree-1:]))!=1):
        raise ValueError('[SPLINE] Expected a finite clamped scalar B-spline. 建议操作：检查节点与控制点。')
    breaks=sorted(set(knots))
    if any(knots.count(t)>degree for t in breaks[1:-1]):
        raise ValueError('[SPLINE_CONTINUITY] Internal multiplicity exceeds degree. 建议操作：使用连续曲线。')
    for position in breaks[1:-1]:
        while knots.count(position)<degree:
            knots,controls=insert_exact(knots,controls,degree,position)
    return [(a,b,controls[j*degree:j*degree+degree+1])
            for j,(a,b) in enumerate(zip(breaks[:-1],breaks[1:]))]


@dataclass(frozen=True)
class Check:
    status: str
    boxes: int
    witness_x: float | None = None
    witness_rational: str | None = None


def verify_ball(knots, controls, degree, radius, tolerance=1e-6,
                max_depth=20, max_boxes=4096):
    """Certify z(x) <= r-sqrt(r²-x²)+tolerance on the whole spline domain.

    PASS is a rational coefficient proof, FAIL contains a rational witness,
    UNKNOWN means a resource bound was reached and MUST NOT be accepted.
    """
    r,eps=rational(radius),rational(tolerance)
    if r<=0 or eps<0 or type(max_depth) is not int or max_depth<0 or type(max_boxes) is not int or max_boxes<1:
        raise ValueError('[CONFIG] Positive radius and legal proof budget required. 建议操作：检查容差和预算。')
    spans=bezier_spans(knots,controls,degree)
    if spans[0][0]<-r or spans[-1][1]>r:
        raise ValueError('[BALL_DOMAIN] Section must lie inside the ball projection. 建议操作：缩小校验域。')
    pending=[]
    for a,b,z in spans:
        upper=[r+eps-c for c in z]
        squared=multiply(upper,upper)
        x_squared=elevate(multiply([a,b],[a,b]),2*degree)
        polynomial=[q+x-r*r for q,x in zip(squared,x_squared)]
        pending.append((a,b,upper,polynomial,0))
    boxes=0
    while pending:
        if boxes>=max_boxes:
            return Check('unknown',boxes)
        a,b,upper,polynomial,depth=pending.pop()
        boxes+=1
        for x,u,v in [(a,upper[0],polynomial[0]),(b,upper[-1],polynomial[-1]),
                      ((a+b)/2,value_at_half(upper),value_at_half(polynomial))]:
            if u<0 or v<0:
                return Check('fail',boxes,float(x),str(x))
        if min(upper)>=0 and min(polynomial)>=0:
            continue
        if depth>=max_depth:
            return Check('unknown',boxes)
        ul,ur=subdivide(upper);pl,pr=subdivide(polynomial);mid=(a+b)/2
        pending.extend([(mid,b,ur,pr,depth+1),(a,mid,ul,pl,depth+1)])
    return Check('pass',boxes)


def contact_budget_bounds(knots,controls,degree,radius,upper_budget,
                          tolerance=1e-6,accuracy=1e-7,max_iterations=40):
    """Bound the necessary vertical L-infinity change; ignores design locks.

    This is a lower bound for the harder locked-design repair problem,
    NOT a statement that an editable shape achieving it exists.
    """
    low,high=F(0),rational(upper_budget)
    if high<0 or rational(accuracy)<=0 or type(max_iterations) is not int or max_iterations<1:
        raise ValueError('[BOUND_CONFIG] Positive accuracy and iteration budget required. 建议操作：检查界定预算。')
    controls=list(map(rational,controls))
    upper_check=verify_ball(knots,[c-high for c in controls],degree,radius,tolerance)
    if upper_check.status!='pass':
        raise ValueError('[UPPER_BOUND] Upper contact envelope is not certified. 建议操作：增大搜索上界。')
    if verify_ball(knots,controls,degree,radius,tolerance).status=='pass':
        return dict(lower_mm=0.,upper_mm=0.,status='bounded',iterations=0)
    for iteration in range(max_iterations):
        if high-low<=rational(accuracy):
            break
        mid=(low+high)/2
        check=verify_ball(knots,[c-mid for c in controls],degree,radius,tolerance)
        if check.status=='pass':high=mid
        elif check.status=='fail':low=mid
        else:
            return dict(lower_mm=float(low),upper_mm=float(high),status='unknown',iterations=iteration+1)
    return dict(lower_mm=float(low),upper_mm=float(high),lower_rational=str(low),
                upper_rational=str(high),status='bounded' if high-low<=rational(accuracy) else 'unknown',
                iterations=iteration+1)


def displacement_bounds(original_knots,original_controls,degree,new_knots,new_controls,
                        accuracy=1e-7,max_boxes=4096):
    """Certified bound on actual geometric displacement, independent of control-net norm."""
    if rational(accuracy)<=0 or type(max_boxes) is not int or max_boxes<1:
        raise ValueError('[BOUND_CONFIG] Positive accuracy and subdivision budget required. 建议操作：检查界定预算。')
    bezier_spans(original_knots,original_controls,degree)
    bezier_spans(new_knots,new_controls,degree)
    t,c=list(map(rational,original_knots)),list(map(rational,original_controls))
    target=list(map(rational,new_knots))
    if t[:degree+1]!=target[:degree+1] or t[-degree-1:]!=target[-degree-1:]:
        raise ValueError('[NESTED_DOMAIN] Expected the same clamped domain. 建议操作：统一参数域。')
    if any(target.count(k)<t.count(k) for k in set(t)):
        raise ValueError('[NESTED_BASIS] New knots must refine the original representation. 建议操作：使用节点插入表示。')
    for knot in sorted(set(target))[1:-1]:
        while t.count(knot)<target.count(knot):t,c=insert_exact(t,c,degree,knot)
    delta=[rational(a)-b for a,b in zip(new_controls,c)]
    spans=bezier_spans(target,delta,degree)
    heap=[];counter=0;low=F(0)
    for _,_,coefficients in spans:
        low=max(low,abs(coefficients[0]),abs(coefficients[-1]),abs(value_at_half(coefficients)))
        heapq.heappush(heap,(-max(map(abs,coefficients)),counter,coefficients));counter+=1
    boxes=0
    while -heap[0][0]-low>rational(accuracy) and boxes<max_boxes:
        _,_,coefficients=heapq.heappop(heap);left,right=subdivide(coefficients);boxes+=1
        for part in (left,right):
            low=max(low,abs(part[0]),abs(part[-1]),abs(value_at_half(part)))
            heapq.heappush(heap,(-max(map(abs,part)),counter,part));counter+=1
    high=-heap[0][0]
    return dict(lower_mm=float(low),upper_mm=float(high),lower_rational=str(low),
                upper_rational=str(high),boxes=boxes,
                status='bounded' if high-low<=rational(accuracy) else 'unknown')
