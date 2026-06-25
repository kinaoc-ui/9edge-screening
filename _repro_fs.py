from pathlib import Path
import first_screen as fs
from first_screen import FirstScreenFilters

f = FirstScreenFilters(
    w1=fs.TfFilter(enabled=True, ma_turn=True, down_vol=True),
    d1=fs.TfFilter(enabled=True, ma_turn=True, down_vol=True),
    require_counter_trend=True,
    require_leading_ma=True,
    ma_flat_min_bars=3,
)
for sym in ['NVDA', 'AAPL']:
    try:
        r = fs.score_symbol(sym, filters=f)
        print(sym, 'OK', r.get('pass') if r else None)
    except Exception as e:
        import traceback
        print(sym, type(e).__name__, e)
        traceback.print_exc()

r = fs.run_screen(Path('screener/new_2026-06-24_8cb43.csv'), limit=3, filters=f, delay=0)
print('run', r.analyzed, r.skipped, r.skip_reasons, r.warning[:100] if r.warning else '')
