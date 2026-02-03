# ARKit to Gaussian Splat Pipeline - Debug Notes

**Date**: 2026-02-02
**Status**: Partially working - splat renders but has quality issues

## What We Built

Complete pipeline: iPhone 3D Scanner App → COLMAP → Brush → Gaussian Splat

### Files Created
- `arkit_to_colmap.py` - Converts ARKit JSON export to COLMAP format
- `run_colmap.sh` - Runs COLMAP feature extraction + triangulation
- `train_splat.sh` - Downloads Brush and runs training
- `convert_brush_ply.py` - Attempts to convert Brush PLY to standard 3DGS format
- `ply_to_splat.py` - Converts PLY to .splat binary format
- `output/viewer.html` - Three.js point cloud viewer

### Results
- **COLMAP**: Successfully triangulated 65,147 3D points from 45 images
- **Reprojection error**: 1.5px (good)
- **Brush training**: Completed 7000 steps in ~5 min on Mac Mini M1
- **Output**: 116,906 Gaussians in `export_7000.ply` (26MB)

## The Problem

The Gaussian splat renders with severe **streaky/spikey artifacts** - elongated splats radiating outward from the scene. This is visible in:
- SuperSplat (see screenshot Jason sent)
- gaussian-splats-3d library
- Custom Three.js viewer shows the point positions are reasonable

## Root Cause Analysis

### Likely: Coordinate System Mismatch
ARKit uses a different coordinate system than COLMAP/3DGS expects:
- ARKit: Y-up, camera looks down -Z
- COLMAP: Various conventions depending on setup
- The poses might need additional rotation/transformation

**Evidence**: Point cloud looks correct, but splat rendering has directional artifacts

### Possible: Pose Matrix Interpretation
We fixed one bug already (row-major vs column-major), but there might be more:
- Rotation vs camera-to-world vs world-to-camera
- Quaternion convention (wxyz vs xyzw)
- Translation in wrong space

### Possible: Training Issues
- 7000 steps might not be enough (default is 30000)
- Brush might have different defaults than original 3DGS

## What We Tried

### 1. Format Conversion (didn't help)
- Wrote `convert_brush_ply.py` to reorder PLY properties to standard 3DGS format
- Result: antimatter15 viewer still showed "RangeError: out of bounds"

### 2. .splat Binary Format (didn't help)
- Wrote `ply_to_splat.py` to convert to simpler binary format
- Result: Same errors in viewers

### 3. Different Viewers Tested
| Viewer | Result |
|--------|--------|
| SuperSplat | Loads but shows spikey artifacts |
| gaussian-splats-3d | Glowing blob, wrong orientation |
| antimatter15/splat | RangeError: out of bounds |
| Luma viewer | Doesn't support external URLs |
| Custom Three.js | Works as point cloud |

### 4. Web Deployment
- Deployed to Vercel: https://dist-lake-kappa-39.vercel.app
- Point cloud viewer works
- Gaussian splat viewers fail due to format issues

## Next Steps to Try

### 1. Fix Coordinate System (Most Likely Solution)
In `arkit_to_colmap.py`, the pose transformation might need adjustment:
```python
# Current: direct use of ARKit pose
# Try: Apply coordinate system rotation
# ARKit Y-up to COLMAP convention
R_convert = np.array([
    [1, 0, 0],
    [0, -1, 0],
    [0, 0, -1]
])
```

### 2. Train Longer
```bash
./brush_app output --total-steps 30000 --export-every 10000
```

### 3. Try gsplat Instead of Brush
gsplat has native Metal support and might handle the data better:
```bash
pip install gsplat
# Has Python API for training
```

### 4. Verify Poses Visually
Add camera frustum visualization to the point cloud viewer to verify poses look correct.

### 5. Compare with Known-Good Data
Download a working 3DGS dataset (like garden scene) and compare the PLY structure.

## Key Files

- **Input**: `scan_data/` (extracted from 3dscan.zip)
- **COLMAP output**: `output/sparse/0/` (cameras.bin, images.bin, points3D.bin)
- **Splat output**: `export_7000.ply` (Brush format, non-standard)
- **Converted**: `standard.ply`, `scene.splat` (our conversion attempts)

## Technical Details

### ARKit Pose Format (from scan_data JSON)
```json
{
  "pose": [16 floats in row-major order],
  "intrinsics": [fx, fy, cx, cy],
  "timestamp": ...
}
```

### Brush PLY Format (non-standard)
Properties in this order (alphabetical, not positional):
```
f_dc_0, f_dc_1, f_dc_2, f_rest_0..44, opacity, rot_0..3, scale_0..2, x, y, z
```

Standard 3DGS expects:
```
x, y, z, nx, ny, nz, f_dc_0..2, f_rest_0..44, opacity, scale_0..2, rot_0..3
```

## Environment

- **Hardware**: Mac Mini M1
- **COLMAP**: 3.13.0 (homebrew)
- **Brush**: v0.3.0 (ArthurBrussee/brush)
- **Python**: 3.14.2

## Links

- GitHub repo: https://github.com/sagharborrum/arkit-to-colmap
- Brush: https://github.com/ArthurBrussee/brush
- gsplat: https://github.com/nerfstudio-project/gsplat
- SuperSplat: https://superspl.at/editor
