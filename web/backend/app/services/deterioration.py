"""
Deterioration Service - Pavement deterioration curve computations.
Implements decay models (like AASHTO/HDM-4) to predict future PCI scores.
"""
from typing import Dict, List

def predict_pci_decay(current_pci: float, structural_type: str, months_future: int, total_crack_area: float = 0.0) -> float:
    """
    Predicts the future PCI score using a decay model:
    PCI(t) = PCI_current - a * (t ** b)
    t is future time in years.
    """
    t = months_future / 12.0
    
    # Check if pavement is rigid (concrete) or flexible (asphalt)
    is_rigid = any(x in structural_type.lower() for x in ["xi măng", "rigid", "concrete", "xm"])
    
    if is_rigid:
        a = 1.2
        b = 1.15
    else:
        a = 1.9
        b = 1.25
        
    # Accelerate deterioration if there is already existing distress (crack area)
    if total_crack_area > 0:
        a += min(4.0, total_crack_area * 0.4)
        
    # Calculate decay
    pci_future = current_pci - a * (t ** b)
    return max(0.0, min(100.0, pci_future))

def get_condition_label(pci: float) -> str:
    """Gets the standard Vietnamese condition description for a PCI score."""
    if pci >= 85:
        return "Rất tốt"
    elif pci >= 70:
        return "Tốt"
    elif pci >= 55:
        return "Trung bình"
    elif pci >= 40:
        return "Kém"
    else:
        return "Rất kém"

def get_pci_predictions(current_pci: float, structural_type: str, total_crack_area: float = 0.0) -> List[Dict]:
    """
    Returns predictions for 6, 12, and 24 months.
    """
    periods = [
        {"months": 6, "label": "6 tháng"},
        {"months": 12, "label": "12 tháng (1 năm)"},
        {"months": 24, "label": "24 tháng (2 năm)"}
    ]
    
    predictions = []
    for p in periods:
        pred_pci = predict_pci_decay(current_pci, structural_type, p["months"], total_crack_area)
        predictions.append({
            "months": p["months"],
            "period": p["label"],
            "predicted_pci": round(pred_pci, 1),
            "condition": get_condition_label(pred_pci)
        })
        
    return predictions
