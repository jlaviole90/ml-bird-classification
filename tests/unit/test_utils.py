"""Unit tests for training utilities."""

from __future__ import annotations

from model.src.utils import AverageMeter, EarlyStopping


class TestAverageMeter:
    def test_single_update(self):
        m = AverageMeter()
        m.update(4.0, n=1)
        assert m.avg == 4.0

    def test_multiple_updates(self):
        m = AverageMeter()
        m.update(2.0, n=2)
        m.update(4.0, n=2)
        assert m.avg == 3.0

    def test_reset(self):
        m = AverageMeter()
        m.update(5.0)
        m.reset()
        assert m.avg == 0.0


class TestEarlyStopping:
    def test_no_stop_when_improving(self):
        es = EarlyStopping(patience=3)
        assert not es(1.0)
        assert not es(0.9)
        assert not es(0.8)

    def test_stop_after_patience(self):
        es = EarlyStopping(patience=2)
        es(1.0)
        assert not es(1.1)
        assert es(1.2)

    def test_counter_resets_on_improvement(self):
        es = EarlyStopping(patience=2)
        es(1.0)
        es(1.1)  # counter = 1
        es(0.5)  # improves → counter = 0
        assert not es(0.6)  # counter = 1
        assert es(0.7)      # counter = 2 → stop
