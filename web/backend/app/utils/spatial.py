import math

def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Calculate the great-circle distance between two GPS coordinates in meters.
    """
    R = 6371000.0  # Earth's radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)
    
    a = (math.sin(delta_phi / 2.0) ** 2 + 
         math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

def distance_point_to_segment(p_lat: float, p_lng: float, a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    """
    Finds the shortest distance in meters from point P (p_lat, p_lng) to line segment AB.
    Uses flat-earth local Cartesian projection for high performance.
    """
    R = 6371000.0
    
    # Convert lat/lng to meters (using latitude A as reference)
    lat_to_m = math.pi * R / 180.0
    lng_to_m = lat_to_m * math.cos(math.radians(a_lat))
    
    # Local coordinate mapping relative to A
    ax, ay = 0.0, 0.0
    bx = (b_lng - a_lng) * lng_to_m
    by = (b_lat - a_lat) * lat_to_m
    px = (p_lng - a_lng) * lng_to_m
    py = (p_lat - a_lat) * lat_to_m
    
    abx = bx - ax
    aby = by - ay
    ab2 = abx * abx + aby * aby
    
    if ab2 == 0:
        return haversine_distance(p_lat, p_lng, a_lat, a_lng)
        
    apx = px - ax
    apy = py - ay
    
    # Projection factor clamped to segment boundary [0, 1]
    t = (apx * abx + apy * aby) / ab2
    t = max(0.0, min(1.0, t))
    
    cx = ax + t * abx
    cy = ay + t * aby
    
    dx = px - cx
    dy = py - cy
    return math.sqrt(dx * dx + dy * dy)

def interpolate_points_along_path(raw_points: list[dict], segment_length_m: float = 100.0) -> list[dict]:
    """
    Applies Linear Referencing System (LRS) interpolation along a polyline path
    to generate coordinate markers at exact segment_length_m intervals.
    """
    if not raw_points:
        return []
        
    interpolated = [raw_points[0]]
    current_target = segment_length_m
    accumulated_dist = 0.0
    
    for i in range(len(raw_points) - 1):
        p1 = raw_points[i]
        p2 = raw_points[i+1]
        dist = haversine_distance(p1["lat"], p1["lng"], p2["lat"], p2["lng"])
        
        while accumulated_dist + dist >= current_target:
            ratio = (current_target - accumulated_dist) / dist
            lat = p1["lat"] + (p2["lat"] - p1["lat"]) * ratio
            lng = p1["lng"] + (p2["lng"] - p1["lng"]) * ratio
            interpolated.append({"lat": lat, "lng": lng})
            current_target += segment_length_m
            
        accumulated_dist += dist
        
    # Append final point if last segment is longer than 15 meters
    if accumulated_dist - (current_target - segment_length_m) > 15.0:
        interpolated.append(raw_points[-1])
        
    return interpolated

