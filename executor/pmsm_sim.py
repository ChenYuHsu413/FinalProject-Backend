# -*- coding: utf-8 -*-
"""簡化版 FMCRD 伺服模擬器 v2 — 讓 L3 參數建議可執行與驗證。

架構(對應論文之 Simulink PMSM 向量控制簡化):
  梯形速度規劃(a_max=P1-42)→ 位置環 P(kp_pos=P1-40)+ 速度前饋
  → 速度環 PI(kp_vel=P1-44)→ 理想化電流環(限流 iq_limit=D3000)
  機械:J·dω/dt = Kt·iq − B·ω − (T_fric + T_load)·sgn(ω)
  劣化注入:T_load = DV × 0.006 Nm(對抗運動之阻力;HI≈3200 → 19.2 Nm)
  滾珠螺桿:rod = θ·lead/2π;輪廓結束且到位 → 煞車鎖定
"""
import numpy as np

KT, J, B = 1.4, 0.012, 0.002
T_FRIC, LEAD = 0.5, 0.010
DT, DV_TO_NM = 1e-4, 0.006
BASE = {'kp_pos': 18.0, 'kp_vel': 0.9, 'ki_vel': 60.0,
        'v_max': 60.0, 'a_max': 600.0, 'iq_limit': 22.0}
# 定版說明:v_max/a_max 使 J·a=7.2Nm 遠低於峰值扭矩(不飽和);
# kp_pos=18 於健康狀態過衝 0.2%(近臨界阻尼);校準:Iq平台 vs DV 線性 r=0.9999


def trapezoid_ref(target, v_max, a_max, n):
    """梯形速度規劃 → 位置/速度參考軌跡。"""
    r = np.zeros(n); v = np.zeros(n)
    pos = vel = 0.0
    d = np.sign(target)
    for k in range(n):
        remain = target - pos
        # 減速距離判斷
        if abs(remain) <= vel**2 / (2*a_max) + 1e-9:
            vel = max(vel - a_max*DT, 0.0)
        elif vel < v_max:
            vel = min(vel + a_max*DT, v_max)
        pos += d*vel*DT
        if d*(target - pos) <= 0: pos, vel = target, 0.0
        r[k], v[k] = pos, d*vel
    return r, v


def simulate_transition(disp_mm=20.0, dv=0.0, gains=None, noise_std=1e-4,
                        t_max=1.0, seed=0):
    g = dict(BASE); g.update(gains or {})
    rng = np.random.default_rng(seed)
    n = int(t_max/DT)
    target = disp_mm/1000/LEAD*2*np.pi
    ref, vref = trapezoid_ref(target, g['v_max'], g['a_max'], n)
    t_load = dv*DV_TO_NM
    th = w = iq_int = 0.0; braked = False
    TH = np.zeros(n); W = np.zeros(n); IQ = np.zeros(n)
    for k in range(n):
        thm = th + rng.normal(0, noise_std)
        if not braked:
            w_cmd = g['kp_pos']*(ref[k]-thm) + vref[k]      # P + 前饋
            e = w_cmd - w
            iq_int = np.clip(iq_int + g['ki_vel']*e*DT, -g['iq_limit'], g['iq_limit'])
            iq = np.clip(g['kp_vel']*e + iq_int, -g['iq_limit'], g['iq_limit'])
            t_res = (T_FRIC + t_load)*np.tanh(w/0.05)
            w += (KT*iq - B*w - t_res)/J*DT
            th += w*DT
            IQ[k] = iq
            if k > 0 and vref[k] == 0 and ref[k] == target and \
               abs(target-th) < 0.002*target and abs(w) < 0.5:
                braked = True
        TH[k] = th; W[k] = w
    return {'t': np.arange(n)*DT, 'theta': TH, 'omega': W, 'iq': IQ,
            'ref': ref, 'vref': vref, 'target': target, 'gains': g}


def metrics(r):
    TH, W, IQ, ref, vref, tgt = r['theta'], r['omega'], r['iq'], r['ref'], r['vref'], r['target']
    ferr = ref - TH                                   # 追隨誤差(對參考軌跡)
    moving = np.abs(vref) > 1e-6
    slew = np.abs(vref) >= 0.999*np.abs(vref).max()   # 真正等速段
    tol = 0.02*tgt
    bad = np.flatnonzero(np.abs(tgt-TH) > tol)
    d = np.sign(tgt)
    return {
      'follow_err_mrad': float(np.sqrt(np.mean(ferr[moving]**2)))*1e3,
      'settle_ms': (bad[-1]+1)*DT*1e3 if len(bad) else 0.0,
      'overshoot_pct': max(float(np.max(d*(TH-tgt))),0.0)/abs(tgt)*100,
      'steady_err_urad': float(np.mean(np.abs((tgt-TH)[int(len(TH)*0.9):])))*1e6,
      'iq_plateau_A': float(np.median(np.abs(IQ[slew]))) if slew.any() else np.nan,
      'iq_sat_frac': float(np.mean(np.abs(IQ[moving]) > 0.99*r['gains']['iq_limit']))}


if __name__ == '__main__':
    print('=== 物理校準:等速段 Iq 平台 vs DV(應線性,重現資料集核心物理)===')
    print(f"{'DV':>6s}{'Iq平台(A)':>11s}{'理論(A)':>10s}")
    for dv in [0, 400, 1600, 3200]:
        r = simulate_transition(dv=dv); m = metrics(r)
        th = (T_FRIC + dv*DV_TO_NM + B*BASE['v_max'])/KT
        print(f"{dv:6.0f}{m['iq_plateau_A']:11.2f}{th:10.2f}")

    print('\n=== 建議→執行→驗證(DV=1600,MED 告警情境)===')
    cases = [
      ('A 健康基線',              0.0,  {}),
      ('B 注入阻力 DV=1600',      1600, {}),
      ('C B+P1-40 +10%(模板建議)',1600, {'kp_pos': BASE['kp_pos']*1.1}),
      ('D B+P1-44 +50%(速度環)',  1600, {'kp_vel': BASE['kp_vel']*1.5, 'ki_vel': BASE['ki_vel']*1.5}),
      ('E B+機構修復(治本)',      0.0,  {}),
    ]
    hdr = f"{'情境':30s}{'追隨誤差(mrad)':>13s}{'整定(ms)':>9s}{'過衝%':>7s}{'穩態(µrad)':>11s}{'Iq平台(A)':>10s}{'飽和比':>7s}"
    print(hdr)
    for name, dv, g in cases:
        m = metrics(simulate_transition(dv=dv, gains=g))
        print(f"{name:32s}{m['follow_err_mrad']:11.1f}{m['settle_ms']:9.0f}{m['overshoot_pct']:7.2f}{m['steady_err_urad']:11.0f}{m['iq_plateau_A']:10.2f}{m['iq_sat_frac']:7.2f}")
