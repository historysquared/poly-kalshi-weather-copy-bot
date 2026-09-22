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


def test_line_length_in_bbox_clips_intersecting_feature():
    # Google returns full LineStrings that intersect the query polygon. Only
    # the segment inside our local bbox should count toward local line length.
    bbox = [-1.0, -1.0, 1.0, 1.0]
    full = [[-2.0, 0.0], [2.0, 0.0]]
    full_km = GoogleContrailsClient.line_length_km(full)
    clipped_km = GoogleContrailsClient.line_length_in_bbox_km(full, bbox)
    assert 440 < full_km < 450
    assert 220 < clipped_km < 225


def test_line_length_in_bbox_excludes_non_intersecting_feature():
    bbox = [-1.0, -1.0, 1.0, 1.0]
    assert GoogleContrailsClient.line_length_in_bbox_km([[2.0, 2.0], [3.0, 3.0]], bbox) == 0.0


def test_nearest_distance_uses_segment_not_only_endpoints():
    # Segment crosses the query point even though both endpoints are ~111 km away.
    d = GoogleContrailsClient.point_to_segment_km(0.0, 0.0, [-1.0, 0.0], [1.0, 0.0])
    assert d is not None and d < 1e-9
