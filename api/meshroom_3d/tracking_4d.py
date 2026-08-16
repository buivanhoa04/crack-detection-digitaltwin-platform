"""Deterministic point-cloud comparison utilities for 4D inspections."""

from __future__ import annotations

import numpy as np


class Tracking4D:
    """Align and compare two Nx3 point sets without external ML dependencies."""

    def __init__(self, reference_points: np.ndarray, observed_points: np.ndarray):
        self.reference = self._validate(reference_points)
        self.observed = self._validate(observed_points)
        if self.reference.shape != self.observed.shape:
            raise ValueError("Point sets must have the same shape and correspondence")

    @staticmethod
    def _validate(points: np.ndarray) -> np.ndarray:
        array = np.asarray(points, dtype=np.float64)
        if array.ndim != 2 or array.shape[1] != 3 or len(array) < 3:
            raise ValueError("Expected at least three 3D points with shape (N, 3)")
        if not np.isfinite(array).all():
            raise ValueError("Point sets must contain only finite values")
        return array

    def align_sessions_cpd(self, max_iterations: int = 15) -> np.ndarray:
        """
        Rigidly align the observed session to the reference with the Kabsch
        solution. The legacy method name is retained for API compatibility.
        """
        del max_iterations  # Closed-form registration does not iterate.
        ref_centroid = self.reference.mean(axis=0)
        obs_centroid = self.observed.mean(axis=0)
        ref_centered = self.reference - ref_centroid
        obs_centered = self.observed - obs_centroid

        covariance = obs_centered.T @ ref_centered
        u, _, vt = np.linalg.svd(covariance)
        rotation = vt.T @ u.T
        if np.linalg.det(rotation) < 0:
            vt[-1, :] *= -1
            rotation = vt.T @ u.T

        return (obs_centered @ rotation.T) + ref_centroid

    @staticmethod
    def calculate_hausdorff_distance(first: np.ndarray, second: np.ndarray) -> float:
        a = Tracking4D._validate(first)
        b = Tracking4D._validate(second)
        distances = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
        return float(max(distances.min(axis=1).max(), distances.min(axis=0).max()))

    @staticmethod
    def detect_progression(
        reference: np.ndarray,
        observed: np.ndarray,
        tolerance: float = 0.05,
    ) -> dict:
        if tolerance < 0:
            raise ValueError("Tolerance must be non-negative")
        distance = Tracking4D.calculate_hausdorff_distance(reference, observed)
        return {
            "status": "ổn định" if distance <= tolerance else "tiến triển",
            "hausdorff_distance_m": distance,
            "tolerance_m": float(tolerance),
        }
