# ARKit to COLMAP Pipeline

Convert iPhone 3D scans (from 3D Scanner App) to COLMAP format for Gaussian splatting.

## Overview

This pipeline takes ARKit pose data from the 3D Scanner App and converts it to COLMAP sparse model format, ready for Gaussian splat training with gsplat or similar tools.

### Pipeline Stages

1. **ARKit → COLMAP** (this repo): Convert ARKit JSON metadata to COLMAP text format
2. **COLMAP Processing**: Feature extraction, matching, and triangulation
3. **Gaussian Splatting**: Train splat model (requires GPU)
4. **Export**: PLY file for viewing in SuperSplat.io, etc.

## Requirements

- **macOS** (tested on macOS 12+)
- **Python 3.10+**
- **COLMAP**: `brew install colmap`

## Installation

```bash
# Clone or navigate to project
cd ~/projects/arkit-to-colmap

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

### Step 1: Convert ARKit Data

```bash
# Basic usage
python arkit_to_colmap.py /path/to/scan_export -o output

# With quality filtering
python arkit_to_colmap.py /path/to/scan_export -o output --quality-threshold 0.9

# Skip frames (every 2nd frame)
python arkit_to_colmap.py /path/to/scan_export -o output --frame-skip 2
```

**Input format:** Directory containing:
- `frame_XXXXX.jpg` - RGB images
- `frame_XXXXX.json` - ARKit metadata (pose, intrinsics, quality)

**Output:**
```
output/
├── images/
│   └── *.jpg
└── sparse/
    └── 0/
        ├── cameras.txt
        ├── images.txt
        └── points3D.txt
```

### Step 2: Run COLMAP

```bash
chmod +x run_colmap.sh
./run_colmap.sh output
```

This runs:
1. Feature extraction (SIFT)
2. Sequential matching
3. Point triangulation (using ARKit poses)
4. Convert to binary format

### Step 3: Train Gaussian Splat

Use the output with gsplat, nerfstudio, or any Gaussian splatting implementation that accepts COLMAP format.

```bash
# Example with gsplat (requires GPU)
python -m gsplat.examples.simple_trainer mcmc \
    --data_dir output \
    --result_dir splat_output \
    --max_steps 7000
```

## Technical Details

### Coordinate System Conversion

ARKit uses Y-up, -Z forward (right-handed).
COLMAP uses Y-down, Z forward (right-handed).

Conversion applies a flip matrix `diag([1, -1, -1, 1])` and inverts to get world-to-camera.

### ARKit JSON Format

```json
{
  "cameraPoseARFrame": [...],  // 4x4 matrix, row-major, camera-to-world
  "intrinsics": [...],          // 3x3 matrix, row-major
  "motionQuality": 0.96,        // 0-1, filter by threshold
  "frame_index": 0
}
```

### COLMAP Files

- **cameras.txt**: PINHOLE model with fx, fy, cx, cy
- **images.txt**: Quaternion (qw, qx, qy, qz) + translation per image
- **points3D.txt**: Initially empty, populated by triangulation

## Troubleshooting

### "No matching image" warnings
The 3D Scanner App saves metadata more frequently than images. This is normal.

### Few frames pass quality filter
Lower the threshold: `--quality-threshold 0.5`

### COLMAP triangulation fails
- Ensure images have sufficient overlap
- Check that poses are reasonable (not all zeros)
- Try bundle adjustment to refine poses

## License

MIT

## Local Gaussian Splat Training (Mac M1+)

After running COLMAP, train a Gaussian splat locally using [Brush](https://github.com/ArthurBrussee/brush):

```bash
./train_splat.sh output 7000
```

This will:
1. Download Brush v0.3.0 (if needed)
2. Train for 7000 steps with live viewer
3. Export `export_7000.ply`

**View your splat:** Open [SuperSplat.io](https://playcanvas.com/supersplat/editor) and drag in your PLY file.

### Tested Results
- Input: 45 images (1920×1440)
- COLMAP: 65,147 3D points
- Training: ~5 minutes on Mac Mini M1
- Output: 26MB PLY file
