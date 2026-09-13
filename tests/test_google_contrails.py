from weather_alpha.providers.google_contrails import GoogleContrailsClient


def test_line_length_reasonable():
    # Roughly one degree longitude near the equator.
    km = GoogleContrailsClient.line_length_km([[0.0, 0.0], [1.0, 0.0]])
    assert 110.0 < km < 112.5


def test_bounds_square_contains_center():
    bounds = GoogleContrailsClient.bounds_square(44.8, -122.8, 150)
    assert len(bounds) == 4
    points = [[float(x) for x in value.split(",")] for value in bounds]
    assert min(point[0] for point in points) < 44.8 < max(point[0] for point in points)
    assert min(point[1] for point in points) < -122.8 < max(point[1] for point in points)


def test_bbox_order_is_google_lon_lat_lon_lat():
    west, south, east, north = GoogleContrailsClient.bbox(44.8, -122.8, 35)
    assert west < -122.8 < east
    assert south < 44.8 < north
