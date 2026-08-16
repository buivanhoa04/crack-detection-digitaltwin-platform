import os
import shutil
import random
from pathlib import Path
from collections import Counter

# Set random seed for reproducibility
random.seed(42)

# Configuration
SOURCE_DATASETS = {
    'd1': r'D:\data\DataCvat\Data_1_OBB(10k_T)',
    'd2': r'D:\data\DataCvat\Data_2_OBB(10k_H)',
    'ds1': r'D:\data\DataCvat\Dataset_1_OBB',
    'ds2': r'D:\data\DataCvat\Dataset_2_OBB'
}

OUTPUT_DIR = Path(r'D:\data\DataCvat\Dataset_Combined_OBB')

CLASS_MAPPING = {
    0: 0, # nut_ca_sau -> nut_ca_sau
    1: 1, # nut_doc -> nut
    2: 1, # nut_ngang -> nut
    3: 2, # o_ga/bong_bat -> o_ga
    4: 3  # khe_noi_be_tong -> khe_noi_be_tong
}

NEW_CLASS_NAMES = {
    0: 'nut_ca_sau',
    1: 'nut',
    2: 'o_ga',
    3: 'khe_noi_be_tong'
}

TRAIN_RATIO = 0.85 # 85% train, 15% val

def build_dataset():
    print("=== Starting Dataset Merging (Option 1: 1:1 Background Ratio) ===")
    
    # 1. Clean / create output directories
    if OUTPUT_DIR.exists():
        print(f"Removing existing output directory: {OUTPUT_DIR}")
        shutil.rmtree(OUTPUT_DIR)
        
    for sub in ['images/train', 'images/val', 'labels/train', 'labels/val']:
        (OUTPUT_DIR / sub).mkdir(parents=True, exist_ok=True)

    labeled_samples = []
    background_samples = []

    # 2. Collect all images & labels from source folders
    for prefix, root_path in SOURCE_DATASETS.items():
        root = Path(root_path)
        img_dir = root / 'images'
        lbl_dir = root / 'labels'
        
        # Find all images recursively
        img_files = [f for f in img_dir.rglob('*.*') if f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.bmp', '.webp']]
        lbl_files = list(lbl_dir.rglob('*.txt'))
        lbl_dict = {f.stem: f for f in lbl_files}
        
        print(f"\nProcessing {prefix} ({root_path}):")
        print(f"  Found {len(img_files)} images, {len(lbl_files)} label files")
        
        for img_p in img_files:
            stem = img_p.stem
            new_name_base = f"{prefix}_{stem}"
            
            lbl_p = lbl_dict.get(stem)
            has_valid_label = False
            label_lines = []
            
            if lbl_p and lbl_p.exists():
                raw_lines = lbl_p.read_text(encoding='utf-8', errors='ignore').splitlines()
                remapped_lines = []
                for line in raw_lines:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split()
                    orig_cls = int(parts[0])
                    if orig_cls in CLASS_MAPPING:
                        new_cls = CLASS_MAPPING[orig_cls]
                        remapped_lines.append(f"{new_cls} " + " ".join(parts[1:]))
                    else:
                        print(f"  [WARNING] Unknown class ID {orig_cls} in {lbl_p}")
                
                if remapped_lines:
                    has_valid_label = True
                    label_lines = remapped_lines
            
            if has_valid_label:
                labeled_samples.append({
                    'img_p': img_p,
                    'new_name_base': new_name_base,
                    'label_lines': label_lines,
                    'prefix': prefix
                })
            else:
                background_samples.append({
                    'img_p': img_p,
                    'new_name_base': new_name_base,
                    'label_lines': [],
                    'prefix': prefix
                })

    total_labeled = len(labeled_samples)
    total_bg_available = len(background_samples)
    
    print(f"\n=== Summary Before Sampling ===")
    print(f"Total Labeled Images: {total_labeled}")
    print(f"Total Background Images Available: {total_bg_available}")
    
    # 3. Option 1: Sample 1:1 background images (7,227 background images)
    num_bg_to_select = min(total_labeled, total_bg_available)
    selected_bg_samples = random.sample(background_samples, num_bg_to_select)
    print(f"Option 1 Selected: {len(selected_bg_samples)} background images (1:1 ratio with labeled images)")

    # 4. Shuffle and split into Train / Val
    random.shuffle(labeled_samples)
    random.shuffle(selected_bg_samples)
    
    labeled_split_idx = int(len(labeled_samples) * TRAIN_RATIO)
    train_labeled = labeled_samples[:labeled_split_idx]
    val_labeled = labeled_samples[labeled_split_idx:]
    
    bg_split_idx = int(len(selected_bg_samples) * TRAIN_RATIO)
    train_bg = selected_bg_samples[:bg_split_idx]
    val_bg = selected_bg_samples[bg_split_idx:]
    
    train_set = train_labeled + train_bg
    val_set = val_labeled + val_bg
    
    random.shuffle(train_set)
    random.shuffle(val_set)
    
    print(f"\n=== Split Counts ===")
    print(f"Train Set: {len(train_set)} images ({len(train_labeled)} labeled + {len(train_bg)} background)")
    print(f"Val Set:   {len(val_set)} images ({len(val_labeled)} labeled + {len(val_bg)} background)")

    # 5. Copy files to destination
    print("\nCopying files to train/val directories...")
    
    class_counter_train = Counter()
    class_counter_val = Counter()
    
    def write_sample(sample, split_name, class_counter):
        dest_img_path = OUTPUT_DIR / 'images' / split_name / f"{sample['new_name_base']}{sample['img_p'].suffix}"
        dest_lbl_path = OUTPUT_DIR / 'labels' / split_name / f"{sample['new_name_base']}.txt"
        
        # Copy image file
        shutil.copy2(sample['img_p'], dest_img_path)
        
        # Write label file
        if sample['label_lines']:
            dest_lbl_path.write_text("\n".join(sample['label_lines']) + "\n", encoding='utf-8')
            for line in sample['label_lines']:
                cls_id = int(line.split()[0])
                class_counter[cls_id] += 1
        else:
            # For background image, write an empty txt file
            dest_lbl_path.write_text("", encoding='utf-8')

    for sample in train_set:
        write_sample(sample, 'train', class_counter_train)
        
    for sample in val_set:
        write_sample(sample, 'val', class_counter_val)

    # 6. Generate data.yaml
    yaml_content = f"""path: {OUTPUT_DIR.as_posix()}
train: images/train
val: images/val

names:
  0: nut_ca_sau
  1: nut
  2: o_ga
  3: khe_noi_be_tong
"""
    (OUTPUT_DIR / 'data.yaml').write_text(yaml_content, encoding='utf-8')

    print("\n=== Dataset Merging Completed Successfully! ===")
    print(f"data.yaml created at {OUTPUT_DIR / 'data.yaml'}")
    print("\nClass Instance Counts in Train Set:")
    for cls_id, count in sorted(class_counter_train.items()):
        print(f"  {cls_id} ({NEW_CLASS_NAMES[cls_id]}): {count}")
        
    print("\nClass Instance Counts in Val Set:")
    for cls_id, count in sorted(class_counter_val.items()):
        print(f"  {cls_id} ({NEW_CLASS_NAMES[cls_id]}): {count}")

if __name__ == '__main__':
    build_dataset()
