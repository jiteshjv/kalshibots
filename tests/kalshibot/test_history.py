import numpy as np
import pytest

from kalshibot.history import BtcHistory

T0 = 1_786_003_200  # minute-aligned


def _arr(n=10_000, seed=1):
    rng = np.random.default_rng(seed)
    a = 60_000 + np.cumsum(rng.normal(0, 3, n))
    a[rng.integers(0, n, 200)] = np.nan
    return a


def test_from_array_matches_numpy():
    a = _arr()
    h = BtcHistory.from_array(T0, a)
    assert h.extreme(T0 + 100, T0 + 900, "y") == np.nanmax(a[100:900])
    assert h.extreme(T0 + 100, T0 + 900, "n") == np.nanmin(a[100:900])
    i = 9000
    seg = a[i - 7200:i]
    assert h.range_2h(T0 + i) == (np.nanmax(seg) - np.nanmin(seg)) / np.nanmean(seg)
    assert h.earliest_ts == T0 and h.latest_ts == T0 + len(a) - 1


def test_range_2h_needs_more_than_3600_seconds():
    a = np.full(8000, np.nan)
    a[:3600] = 100.0
    h = BtcHistory.from_array(T0, a)
    assert h.range_2h(T0 + 7200) is None          # exactly 3600 -> None (her: > 3600)
    a[3600] = 101.0
    h = BtcHistory.from_array(T0, a)
    assert h.range_2h(T0 + 7200) == pytest.approx(1 / np.mean(a[:3601]))
    assert h.range_2h(T0 + 100) is None           # short history


def test_extreme_all_nan_is_none_and_price_at():
    a = np.array([1.0, np.nan, np.nan, 4.0, np.nan])
    h = BtcHistory.from_array(T0, a)
    assert h.extreme(T0 + 1, T0 + 3, "y") is None
    assert h.price_at(T0 + 2) == 1.0
    assert h.price_at(T0 + 2, max_age=0) is None
    assert h.price_at(T0 + 4) == 4.0
    assert h.price_at(T0 + 3, max_age=0) == 4.0
    assert h.price_at(T0 - 1) is None


def test_swept_like_her_trades():
    a = np.full(2000, 100.0)
    h = BtcHistory.from_array(T0, a)
    t = T0 + 1500
    assert not h.swept(t, 1, 600)                  # flat: not beyond
    a2 = a.copy(); a2[1400] = 101.0                # new high in the last 5 min
    h2 = BtcHistory.from_array(T0, a2)
    assert h2.swept(t, 1, 600) and not h2.swept(t, -1, 600)
    assert not h2.swept(t, 1, 1300)                # window starts before the history -> False
    a3 = a.copy(); a3[1199] = 99.0                 # t-301 is in the prior window, not the last 5 min
    assert not BtcHistory.from_array(T0, a3).swept(t, -1, 600)
    a3[1200] = 98.0                                # t-300 is in the last 5 min
    assert BtcHistory.from_array(T0, a3).swept(t, -1, 600)


def test_add_trim_and_covers():
    h = BtcHistory(max_days=0.5)
    for i in range(0, 86400, 7):
        h.add(T0 + i + 0.4, 100 + i * 1e-4)
    assert h.latest_ts == T0 + 86394
    assert h.covers(43000) and not h.covers(64900)
    assert h.price_at(T0 + 86395) == pytest.approx(100 + 86394e-4)
    assert h.extreme(T0 + 86000, T0 + 86400, "y") == pytest.approx(100 + 86394e-4)
    h.add(T0 - 5, 1.0)                              # too old: ignored
    assert h.extreme(T0, T0 + 86400, "n") > 2


def test_minute_bars_before_seconds():
    h = BtcHistory()
    for k in range(180):
        h.add_minute_bar(T0 + 60 * k, 101 + (k == 50), 99 - (k == 60), 100)
    assert h.extreme(T0, T0 + 3 * 3600, "y") == 102
    assert h.extreme(T0, T0 + 3 * 3600, "n") == 98
    assert h.range_2h(T0 + 3 * 3600) == pytest.approx((101 - 98) / 100)   # last 2h: minutes 60..179
    for i in range(10):
        h.add(T0 + 3 * 3600 + i, 103.0)
    assert h.extreme(T0, T0 + 4 * 3600, "y") == 103
    assert h.price_at(T0 + 30) == 100
    assert h.covers(3 * 3600)


def test_per_second_history_survives_a_restart(tmp_path):
    import numpy as np
    from kalshibot.history import BtcHistory
    h = BtcHistory()
    for i in range(1200):                                   # 20 min of per-second prices
        h.add(1_000_000 + i, 100.0 + i * 0.01)
    p = tmp_path / "secs.npz"
    assert h.save_seconds(p, last_s=1800) == 1200
    h2 = BtcHistory()
    assert h2.load_seconds(p, now=1_000_000 + 1199 + 30) == 1200      # restarted 30 s later
    assert np.allclose(h2._sec_slice(1_000_000, 1_001_200), h._sec_slice(1_000_000, 1_001_200))
    h3 = BtcHistory()
    assert h3.load_seconds(p, now=1_000_000 + 1199 + 3600) == 0       # an hour later: too old, not used
