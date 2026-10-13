import math

import pytest

from faceeval.stats import Rate, betainc, binom_cdf, clopper_pearson, percentile


def test_incomplete_beta_matches_closed_forms():
    # I_x(1, 1) = x; I_x(a, 1) = x^a; I_x(1, b) = 1 - (1-x)^b
    assert betainc(1, 1, 0.3) == pytest.approx(0.3, abs=1e-12)
    assert betainc(3, 1, 0.4) == pytest.approx(0.4 ** 3, abs=1e-12)
    assert betainc(1, 4, 0.2) == pytest.approx(1 - 0.8 ** 4, abs=1e-12)


def test_binomial_cdf_against_direct_sum():
    n, p = 30, 0.17
    for k in (0, 3, 10, 29):
        direct = sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))
        assert binom_cdf(k, n, p) == pytest.approx(direct, rel=1e-10)


def test_clopper_pearson_reference_values():
    # Zero failures in 100: upper bound is 1 - 0.025**(1/100).
    low, high = clopper_pearson(0, 100)
    assert low == 0.0
    assert high == pytest.approx(1 - 0.025 ** (1 / 100), abs=1e-9)
    low, high = clopper_pearson(5, 100)
    assert low == pytest.approx(0.016431, abs=1e-5)
    assert high == pytest.approx(0.112835, abs=1e-5)
    low, high = clopper_pearson(10, 10)
    assert low == pytest.approx(0.025 ** (1 / 10), abs=1e-9)
    assert high == 1.0


def test_rule_of_three_needs_300_attempts_for_one_percent():
    assert clopper_pearson(0, 300)[1] < 0.0125
    assert clopper_pearson(0, 100)[1] > 0.03


def test_percentile_matches_numpy_linear():
    values = [10, 1, 7, 3]
    assert percentile(values, 50) == pytest.approx(5.0)
    assert percentile(values, 95) == pytest.approx(9.55)
    assert math.isnan(percentile([], 50))


def test_rate_formatting():
    assert Rate(0, 0).fmt() == "无样本"
    assert "1/4" in Rate(1, 4).fmt()
