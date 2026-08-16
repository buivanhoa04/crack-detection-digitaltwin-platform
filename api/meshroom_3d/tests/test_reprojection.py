import numpy as np
import os
import sys

# Add the Meshroom source directory to the import path.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tracking_4d import Tracking4D

def test_icp_and_hausdorff():
    """Verifies that the SVD-based ICP registration and Hausdorff distance operate correctly."""
    # 1. Reference line (Session 1)
    s1 = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [2.0, 2.0, 2.0]], dtype=np.float32)
    
    # 2. Session 2 line shifted by 5cm (0.05m) GPS drift
    drift = np.array([0.05, 0.05, 0.05], dtype=np.float32)
    s2 = s1 + drift
    
    # Initialize Tracking4D
    tracker = Tracking4D(s1, s2)
    
    # Register / Align Session 2 with Session 1
    s2_aligned = tracker.align_sessions_cpd(max_iterations=15)
    
    # Compute final alignment error
    alignment_error = np.mean(np.linalg.norm(s2_aligned - s1, axis=1))
    assert alignment_error < 0.01, f"Expected error < 0.01, got {alignment_error:.4f}"
    
    # Compute Hausdorff distance post-alignment
    h_dist = tracker.calculate_hausdorff_distance(s1, s2_aligned)
    assert h_dist < 0.01, f"Expected Hausdorff < 0.01, got {h_dist:.4f}"
    
    # Detect progression
    prog = tracker.detect_progression(s1, s2_aligned, tolerance=0.05)
    assert prog["status"] == "ổn định", f"Expected stable status, got {prog['status']}"
    
    print("SUCCESS: test_icp_and_hausdorff passed successfully!")


if __name__ == "__main__":
    test_icp_and_hausdorff()
