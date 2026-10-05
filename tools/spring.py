#!/usr/bin/env python3
"""Damped-spring easing curves as CSS linear() strings (Halo Motion).

The spring is normalised so it settles (envelope < 0.2%) exactly at t = 1, i.e. the CSS
duration is the settle time.  zeta = damping ratio; overshoot = exp(-zeta*pi/sqrt(1-zeta^2)).
Usage: spring.py            -> prints the three tokens
       spring.py --check    -> prints overshoot per curve
"""
import math, sys

SPRINGS = {
    # name: (damping ratio, samples)
    'snappy': (0.92, 24),   # micro interactions: no visible overshoot (0.03%)
    'smooth': (0.82, 28),   # standard: ~1.1% overshoot
    'gentle': (0.74, 36),   # hero shots: ~3.4% overshoot, a soft settle
}
EPS = 0.002

def curve(zeta, t):
    w0 = math.log(1 / EPS) / zeta
    wd = w0 * math.sqrt(1 - zeta * zeta)
    a = zeta * w0
    return 1 - math.exp(-a * t) * (math.cos(wd * t) + (a / wd) * math.sin(wd * t))

def linear(zeta, n):
    pts = [round(curve(zeta, i / n), 3) for i in range(n + 1)]
    pts[-1] = 1
    return 'linear(' + ', '.join(f'{v:g}' for v in pts) + ')'

if __name__ == '__main__':
    for name, (z, n) in SPRINGS.items():
        if '--check' in sys.argv:
            peak = max(curve(z, i / 400) for i in range(401))
            print(f'{name}: zeta={z} overshoot={100 * (peak - 1):.2f}%')
        else:
            print(f'--spring-{name}: {linear(z, n)};')
