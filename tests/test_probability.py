from weather_alpha.probability import gaussian_bucket_probability, gaussian_above_probability

def test_bucket_probability_centered():
    p = gaussian_bucket_probability(90.0, 1.0, 90.0, 90.0)
    assert 0.35 < p < 0.40

def test_above_monotonic():
    assert gaussian_above_probability(92, 1, 90) > gaussian_above_probability(90, 1, 90)
