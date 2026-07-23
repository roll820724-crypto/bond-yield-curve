#!/usr/bin/env python3
"""预计算模式三所有时序，输出 data_m3_computed.json。
前端渲染直接从文件读，不做任何 JS 计算。
cron: 每日 17:30 或手动运行。

输出结构:
{
  "computed_date": "2026-07-22",   # 最新交易日
  "dates": ["2019-01-02", ...],    # 从 2019 开始的日期序列
  "lpr5y": [4.85, ...],            # forward-filled LPR 5Y
  "deposit5y": [2.75, ...],        # forward-filled 定存 5Y
  "corridor_mid": [...],           # (LPR+Deposit)/2
  "corridor_6mma": [...],          # 126日MA
  "cgb_10y": [1.73, ...],          # 国债10Y到期
  "tax_premium": [8.6, ...],       # 税收溢价 bp
  "base_return": [...],            # 基础回报
  "br_250ma": [...],               # 250MA
  "br_750ma": [...],               # 750MA
  "base_level": [...],             # MIN(250MA,750MA)
  "M": [...],                      # 参考利率M
  "N": [...],                      # 预定利率N
  "latest": {                      # 摘要卡片用
    "M": ..., "N": ..., "corridor_6mma": ..., "base_level": ...
  }
}
"""

import json, os
from pathlib import Path

DIR = Path(__file__).parent

# ─── 分段调节系数（与 JS 一致） ───
SEGMENTS = [
    (4.0, float('inf'), 0.20, None,  4.0),
    (3.5, 4.0,         0.30, None,  3.5),
    (3.0, 3.5,         0.50, 2.95,  3.0),
    (2.5, 3.0,         0.95, 2.475, 2.5),
    (2.0, 2.5,         0.95, 2.0,   2.0),
    (1.0, 2.0,         1.00, 1.0,   1.0),
    (0.0, 1.0,         1.00, 0.0,   0.0),
]

def adj_N(M):
    """分段调节：M → N"""
    for lo, hi, coef, base, threshold in SEGMENTS:
        if lo < M <= hi:
            if base is None:
                return M * coef
            return base + (M - threshold) * coef
    return M

def rolling_mean(arr, window):
    """移动平均，忽略 None"""
    out = [None] * len(arr)
    s, cnt = 0.0, 0
    for i, v in enumerate(arr):
        if v is not None:
            s += v
            cnt += 1
        if i >= window and arr[i - window] is not None:
            s -= arr[i - window]
            cnt -= 1
        if cnt > 0:
            out[i] = round(s / cnt, 6)
    return out

def ffill_daily(daily_dates, sparse_dates, sparse_vals):
    """稀疏月度数据 → 逐日前向填充"""
    out = [None] * len(daily_dates)
    last = None
    j = 0
    for i, d in enumerate(daily_dates):
        while j < len(sparse_dates) and sparse_dates[j] <= d:
            if sparse_vals[j] is not None:
                last = sparse_vals[j]
            j += 1
        out[i] = last
    return out


def compute():
    # ── 加载数据 ──
    with open(DIR / 'data_gov_ytm.json') as f: gov = json.load(f)
    with open(DIR / 'data_cdb_ytm.json') as f: cdb = json.load(f)
    with open(DIR / 'data_lpr5y.json') as f:  lpr = json.load(f)
    with open(DIR / 'data_deposit5y.json') as f: dep = json.load(f)

    dates = gov['dates']
    g9_idx = gov['terms'].index('9Y')
    g10_idx = gov['terms'].index('10Y')
    c9_idx = cdb['terms'].index('9Y')

    g_date_map = {d: i for i, d in enumerate(gov['dates'])}
    n = len(dates)

    # ── Step 1: 税收溢价 & 基础回报 ──
    tax = [None] * n
    base_return = [None] * n
    cgb10y = [None] * n

    for i, d in enumerate(cdb['dates']):
        gi = g_date_map.get(d)
        if gi is None: continue
        cv = cdb['rows'][i][c9_idx]
        gv = gov['rows'][gi][g9_idx]
        gv10 = gov['rows'][gi][g10_idx]
        if cv is not None and gv is not None:
            tax[gi] = round((cv - gv) * 100, 2)
        if tax[gi] is not None and gv10 is not None:
            base_return[gi] = round(gv10 + tax[gi] / 100, 6)
        cgb10y[gi] = gv10

    # ── Step 2: MA 基础回报水平 ──
    br250 = rolling_mean(base_return, 250)
    br750 = rolling_mean(base_return, 750)
    base_level = [min(a, b) if a is not None and b is not None else None
                  for a, b in zip(br250, br750)]

    # ── Step 3: 利率走廊 ──
    lpr_ff = ffill_daily(dates, lpr['dates'], lpr['values'])
    dep_ff = ffill_daily(dates, dep['dates'], dep['values'])
    corridor_mid = [round((l + d) / 2, 6) if l is not None and d is not None else None
                    for l, d in zip(lpr_ff, dep_ff)]
    corridor_6mma = rolling_mean(corridor_mid, 126)

    # ── Step 4: M = MIN(corridor, base_level) ──
    M = [min(c, b) if c is not None and b is not None else None
         for c, b in zip(corridor_6mma, base_level)]

    # ── Step 5: N = 分段调节 ──
    N = [round(adj_N(m), 6) if m is not None else None for m in M]

    # ── 截取 2019 起（图表展示起点） ──
    try:
        start = next(i for i, d in enumerate(dates) if d >= '2019-01-01')
    except StopIteration:
        start = 0

    def nonull(arr):
        return [v if v is not None else None for v in arr[start:]]

    # ── 最新值 ──
    def last(arr):
        for v in reversed(arr):
            if v is not None:
                return v
        return None

    computed_date = dates[-1]

    result = {
        "computed_date": computed_date,
        "dates": dates[start:],
        "lpr5y": nonull(lpr_ff),
        "deposit5y": nonull(dep_ff),
        "corridor_mid": nonull(corridor_mid),
        "corridor_6mma": nonull(corridor_6mma),
        "cgb_10y": nonull(cgb10y),
        "tax_premium": nonull(tax),
        "base_return": nonull(base_return),
        "br_250ma": nonull(br250),
        "br_750ma": nonull(br750),
        "base_level": nonull(base_level),
        "M": nonull(M),
        "N": nonull(N),
        "latest": {
            "M": last(M),
            "N": last(N),
            "corridor_6mma": last(corridor_6mma),
            "base_level": last(base_level),
            "lpr5y": last(lpr_ff),
            "deposit5y": last(dep_ff),
            "cgb_10y": last(cgb10y),
            "tax_premium": last(tax),
            "base_return": last(base_return),
        },
    }

    out_path = DIR / 'data_m3_computed.json'
    with open(out_path, 'w') as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"✅ 预计算完成 → {out_path} ({len(result['dates'])} 天, {computed_date})")


if __name__ == '__main__':
    compute()
