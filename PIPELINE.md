# ARKit → COLMAP → Gaussian Splat: Complete Pipeline Documentation

> End-to-end process for converting iPhone 3D Scanner App captures into browser-viewable Gaussian splats.

**Live Demo:** https://splat-viewer-3dgs.vercel.app  
**Source:** https://github.com/sagharborrum/arkit-to-colmap

---

## Table of Contents

1. [Pipeline Overview](#pipeline-overview)
2. [Step 1: Capture with 3D Scanner App](#step-1-capture-with-3d-scanner-app)
3. [Step 2: Export & Transfer](#step-2-export--transfer)
4. [Step 3: ARKit → COLMAP Format Conversion](#step-3-arkit--colmap-format-conversion)
5. [Step 4: COLMAP Structure-from-Motion](#step-4-colmap-structure-from-motion)
6. [Step 5: Gaussian Splat Training (CUDA GPU)](#step-5-gaussian-splat-training-cuda-gpu)
7. [Step 6: Post-Processing & Compression](#step-6-post-processing--compression)
8. [Step 7: Web Viewer Deployment](#step-7-web-viewer-deployment)
9. [Data Formats Reference](#data-formats-reference)
10. [Lessons Learned & Gotchas](#lessons-learned--gotchas)
11. [Automation Architecture (SvelteKit + Firebase)](#automation-architecture-sveltekit--firebase)

---

## Pipeline Overview

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   iPhone     │    │   Export     │    │   ARKit →    │    │   COLMAP     │    │   gsplat     │    │   Web        │
│   3D Scanner │───▶│   ZIP with   │───▶│   COLMAP     │───▶│   SfM        │───▶│   Training   │───▶│   Viewer     │
│   App        │    │   JSON+JPG   │    │   Converter  │    │   Pipeline   │    │   (CUDA)     │    │   (Three.js) │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
     iPhone              AirDrop           Python script       COLMAP CLI       Cloud GPU (RunPod)    Vercel/CDN
                         ~86MB             arkit_to_colmap.py   ~2 min           ~45 min               Static host
```

**Total pipeline time:** ~1 hour (mostly GPU training)  
**Total cost:** ~$0.15 (RunPod community cloud)

---

## Step 1: Capture with 3D Scanner App

**App:** [3D Scanner App](https://apps.apple.com/app/3d-scanner-app/id1419913995) (iOS, free)

### Capture Tips
- Walk slowly around the object (full 360° if possible)
- Keep the object centered in frame
- Maintain consistent distance (~0.5-1m for small objects)
- Good lighting, avoid harsh shadows
- The app shows a real-time point cloud — fill in gaps

### What the App Records Per Frame
- **RGB image** (1920×1440 JPG)
- **Depth map** (EXR format, from LiDAR)
- **Confidence map** (PNG, per-pixel depth confidence)
- **Camera pose** (4×4 matrix, ARKit world coordinates)
- **Camera intrinsics** (3×3 matrix)
- **Motion quality** (0-1 score from ARKit)
- **GPS coordinates** (if available)

### Our Test Capture
- **Subject:** Glass bottle on countertop
- **Frames captured:** 269
- **Frames with RGB images:** 45 (app saves JPGs at keyframe intervals)
- **Capture duration:** ~30 seconds
- **Raw export size:** 86MB

---

## Step 2: Export & Transfer

### From 3D Scanner App
1. Open the scan in the app
2. Tap **Share** → **All Data**
3. Choose export method (AirDrop to Mac recommended)
4. You'll get a ZIP file

### Export Structure
```
scan_export/
├── 2026_01_13_14_47_59/          # Timestamp folder
│   ├── frame_00000.json          # Camera metadata (269 files)
│   ├── frame_00000.jpg           # RGB image (45 files, keyframes only)
│   ├── frame_00000.exr           # Depth map (269 files)
│   ├── conf_00000.png            # Confidence map (269 files)
│   ├── annotations.json          # Scene annotations
│   ├── world_map.arkit           # ARKit world map
│   ├── textured_output.obj       # Mesh export
│   ├── textured_output.mtl       # Mesh material
│   └── textured_output_0.png     # Mesh texture
```

### Key: Not Every Frame Has an Image
The app captures metadata (JSON) for every frame (~269), but only saves JPG images at keyframe intervals (~45). The pipeline only uses frames that have both JSON metadata AND a JPG image.

---

## Step 3: ARKit → COLMAP Format Conversion

**Script:** `arkit_to_colmap.py`  
**Input:** Scan export folder  
**Output:** COLMAP-compatible directory structure

### What It Does
1. Reads each `frame_XXXXX.json` metadata file
2. Extracts camera pose (4×4 matrix) and intrinsics (fx, fy, cx, cy)
3. Filters by motion quality threshold (default: 0.8)
4. Matches JSON files to their JPG images
5. Converts ARKit coordinate system to COLMAP convention
6. Writes COLMAP text-format sparse model files

### Usage
```bash
python arkit_to_colmap.py scan_data/2026_01_13_14_47_59/ -o output_colmap
```

### ARKit JSON Format (per frame)
```json
{
    "cameraPoseARFrame": [
        -0.0358, 0.9932, -0.1098, 1.5005,   // Row 0: rotation + tx
        -0.7628, 0.0437,  0.6450, -1.1476,   // Row 1: rotation + ty
         0.6455, 0.1069,  0.7561,  6.0612,   // Row 2: rotation + tz
         0,      0,       0,       1.0        // Row 3: homogeneous
    ],
    "intrinsics": [
        1348.93, 0,       961.14,            // fx,  0, cx
        0,       1348.93, 718.69,            // 0,  fy, cy
        0,       0,       1                  // 0,   0,  1
    ],
    "motionQuality": 1,
    "frame_index": 0
}
```

### ⚠️ Critical: Pose Matrix is Row-Major

ARKit stores the 4×4 camera-to-world matrix as a **flat 16-element array in row-major order**.

```python
# CORRECT ✅
pose = np.array(pose_flat).reshape(4, 4, order='C')  # Row-major

# WRONG ❌ — produces garbage splats
pose = np.array(pose_flat).reshape(4, 4, order='F')  # Column-major
```

### Coordinate System Conversion

ARKit and COLMAP use different coordinate conventions:

```
ARKit:                    COLMAP:
  Y ↑                      Z (forward)
  |                         |
  |                         |
  +——→ X                    +——→ X
 /                         /
Z (toward viewer)         Y (down)
```

**ARKit:** Y-up, camera looks down -Z, right-handed  
**COLMAP:** Y-down, camera looks down +Z, right-handed

```python
def arkit_to_colmap_pose(c2w_arkit):
    # Flip Y and Z axes
    flip = np.diag([1.0, -1.0, -1.0, 1.0])
    c2w_colmap = flip @ c2w_arkit @ flip
    
    # Invert: camera-to-world → world-to-camera (what COLMAP stores)
    w2c = np.linalg.inv(c2w_colmap)
    
    # Extract rotation → quaternion (COLMAP uses qw, qx, qy, qz)
    R = w2c[:3, :3]
    t = w2c[:3, 3]
    quat = Rotation.from_matrix(R).as_quat()  # Returns [qx,qy,qz,qw]
    qw, qx, qy, qz = quat[3], quat[0], quat[1], quat[2]
    
    return qw, qx, qy, qz, t[0], t[1], t[2]
```

### Output: COLMAP Text Format

The script generates three files in `output_dir/sparse/0/`:

**`cameras.txt`** — Single shared camera (all frames from same device):
```
# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]
1 PINHOLE 1920 1440 1374.718750 1377.531250 960.375000 720.093750
```

**`images.txt`** — Two lines per image (pose + empty 2D points):
```
# IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME
1 0.7071 0.0 0.7071 0.0 1.5 -1.1 6.0 1 frame_00000.jpg
                                    # Empty line (2D points populated by COLMAP)
```

**`points3D.txt`** — Empty (populated by COLMAP triangulation):
```
# POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[]
```

The script also copies all matched JPG images into `output_dir/images/`.

---

## Step 4: COLMAP Structure-from-Motion

**Tool:** COLMAP 3.13.0 (`brew install colmap`)  
**Script:** `run_colmap.sh`  
**Input:** Output from Step 3  
**Output:** Sparse 3D point cloud with refined camera poses

### Why SfM Instead of ARKit Poses?

We originally tried using ARKit poses directly (just triangulating points). This produced 65k 3D points but the resulting Gaussian splats had severe artifacts. **Running full COLMAP SfM from scratch** lets COLMAP:

1. Re-estimate camera poses with bundle adjustment (more precise)
2. Produce higher-quality 3D points
3. Generate proper feature tracks for Gaussian splat training

### Pipeline Steps

```bash
# run_colmap.sh <project_dir>

PROJECT_DIR="output_colmap"

# 1. Feature Extraction — Detect keypoints in every image
colmap feature_extractor \
    --database_path "$PROJECT_DIR/database.db" \
    --image_path "$PROJECT_DIR/images" \
    --ImageReader.camera_model PINHOLE \
    --ImageReader.single_camera 1

# 2. Feature Matching — Find correspondences between image pairs
colmap sequential_matcher \
    --database_path "$PROJECT_DIR/database.db" \
    --SequentialMatching.overlap 10

# 3. Triangulation — Compute 3D points using ARKit poses as initialization
colmap point_triangulator \
    --database_path "$PROJECT_DIR/database.db" \
    --image_path "$PROJECT_DIR/images" \
    --input_path "$PROJECT_DIR/sparse/0" \
    --output_path "$PROJECT_DIR/sparse/0"

# 4. Model Statistics
colmap model_analyzer --path "$PROJECT_DIR/sparse/0"

# 5. Convert to Binary (required by gsplat)
colmap model_converter \
    --input_path "$PROJECT_DIR/sparse/0" \
    --output_path "$PROJECT_DIR/sparse/0" \
    --output_type BIN
```

### Alternative: Full SfM (Recommended)

For best quality, bypass ARKit poses entirely and let COLMAP do full SfM:

```bash
# Feature extraction (same as above)
colmap feature_extractor \
    --database_path "$PROJECT_DIR/database.db" \
    --image_path "$PROJECT_DIR/images" \
    --ImageReader.camera_model PINHOLE \
    --ImageReader.single_camera 1

# Exhaustive matching (better for small datasets <100 images)
colmap exhaustive_matcher \
    --database_path "$PROJECT_DIR/database.db"

# Full SfM mapper (estimates poses from scratch)
mkdir -p "$PROJECT_DIR/sparse"
colmap mapper \
    --database_path "$PROJECT_DIR/database.db" \
    --image_path "$PROJECT_DIR/images" \
    --output_path "$PROJECT_DIR/sparse"

# Convert to binary
colmap model_converter \
    --input_path "$PROJECT_DIR/sparse/0" \
    --output_path "$PROJECT_DIR/sparse/0" \
    --output_type BIN
```

### Our Results (Full SfM)
- **Registered images:** 45/45 (100%)
- **3D points:** 55,012
- **Reprojection error:** 0.82 pixels (excellent)
- **Observations:** 310,411
- **Track length (mean):** 5.6
- **Track length (median):** 5
- **Seen by ≥3 images:** 98.6%
- **Camera intrinsics:** PINHOLE 1920×1440, focal 1374.7/1377.5, FoV 70°/55°

### Output Files
```
output_colmap_sfm/
├── database.db              # SQLite (features, matches, geometry)
├── images/                  # 45 JPG images (1920×1440)
│   ├── frame_00000.jpg
│   ├── frame_00001.jpg
│   └── ...
└── sparse/
    └── 0/
        ├── cameras.bin      # Camera model
        ├── images.bin       # Image poses (refined by SfM)
        └── points3D.bin     # 55,012 3D points
```

---

## Step 5: Gaussian Splat Training (CUDA GPU)

**Library:** [gsplat](https://github.com/nerfstudio-project/gsplat) v1.5.3  
**Hardware:** NVIDIA GPU with CUDA (Metal/MPS does NOT work — see [Gotchas](#lessons-learned--gotchas))  
**Cloud GPU:** RunPod RTX A5000 (24GB VRAM), $0.16/hr community cloud

### ⚠️ Metal/MPS GPU Training is Broken

We extensively tested [OpenSplat](https://github.com/pierotofy/OpenSplat) with Metal GPU on Mac Mini M1. **Every configuration produced garbage** — 99%+ of Gaussians were near-transparent with spikey artifacts. The same viewer renders CUDA-trained splats perfectly. **You must use an NVIDIA GPU with CUDA.**

### Cloud GPU Setup (RunPod)

```bash
# 1. Create pod via RunPod dashboard or API
# Template: RunPod PyTorch 2.x
# GPU: RTX A5000 (24GB) — $0.16/hr community cloud
# Disk: 20GB container + 50GB volume

# 2. SSH into pod
ssh root@<pod_ip> -p <port>

# 3. Upload COLMAP data
# On local machine:
cd ~/projects/arkit-to-colmap
tar -czf colmap_data.tar.gz -C output_colmap_sfm sparse
tar -czf colmap_images.tar.gz -C output_colmap_sfm images
scp -P <port> colmap_data.tar.gz colmap_images.tar.gz root@<pod_ip>:/workspace/

# On pod:
cd /workspace
mkdir -p output_colmap_sfm
tar -xzf colmap_data.tar.gz -C output_colmap_sfm
tar -xzf colmap_images.tar.gz -C output_colmap_sfm
```

### Install Dependencies (on pod)

```bash
pip install gsplat==1.5.3

# Clone gsplat repo for the training script (MUST match pip version)
git clone https://github.com/nerfstudio-project/gsplat.git
cd gsplat
git checkout v1.5.3  # ⚠️ Main branch examples are incompatible with v1.5.3

# Install example dependencies
pip install imageio tyro fused-ssim
```

### Create Downscaled Images

gsplat's `--data_factor` flag expects a pre-existing `images_N/` directory. It does NOT auto-downscale.

```bash
cd /workspace/output_colmap_sfm
mkdir images_2

# Downscale all images to 50% (960×720)
for f in images/*.jpg; do
    base=$(basename "$f")
    convert "$f" -resize 50% "images_2/$base"
done
# Or with Python/Pillow if ImageMagick unavailable
```

### Run Training

```bash
cd /workspace/gsplat

python3 examples/simple_trainer.py default \
    --data_dir /workspace/output_colmap_sfm \
    --data_factor 2 \
    --result_dir /workspace/results \
    --max_steps 30000

# Output: /workspace/results/ckpt_29999_rank0.pt (PyTorch checkpoint)
#         Trained PLY can be exported from checkpoint
```

### Training Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| `--data_factor` | 2 | Downscale factor (2 = half resolution, 960×720) |
| `--max_steps` | 30000 | Standard for good quality |
| `--sh_degree` | 3 | Spherical harmonics degree (default) |
| Initial Gaussians | 55,012 | One per COLMAP 3D point |
| Final Gaussians | ~6.2M | After densification |

### Our Results
- **Training time:** ~45 minutes on RTX A5000
- **Speed:** ~10-11 iterations/second
- **Final loss:** ~0.01-0.035
- **Gaussians:** 55,012 → 6,198,455 (after densification)
- **Cost:** ~$0.12 total (45 min × $0.16/hr)

### Extract PLY from Checkpoint

gsplat saves PyTorch checkpoints, not PLY files directly. To export:

```python
import torch
import numpy as np
from plyfile import PlyData, PlyElement

# Load checkpoint
ckpt = torch.load("results/ckpt_29999_rank0.pt", map_location="cpu")

# Extract Gaussian parameters
means = ckpt["splats"]["means"].numpy()        # (N, 3) positions
scales = ckpt["splats"]["scales"].numpy()       # (N, 3) log-scales
quats = ckpt["splats"]["quats"].numpy()         # (N, 4) quaternions
opacities = ckpt["splats"]["opacities"].numpy() # (N,) logit-opacities
sh0 = ckpt["splats"]["sh0"].numpy()             # (N, 1, 3) DC color
shN = ckpt["splats"]["shN"].numpy()             # (N, K, 3) higher-order SH

# Write to standard 3DGS PLY format
# ... (see ply_to_splat.py for format details)
```

---

## Step 6: Post-Processing & Compression

### PLY → .splat Conversion

The `.splat` format is a compact binary format used by web viewers. Each Gaussian is 32 bytes:

```
Per Gaussian (32 bytes):
├── Position:   3 × float32 (12 bytes) — x, y, z
├── Scale:      3 × float32 (12 bytes) — sx, sy, sz (exp of log-scale)
├── Color:      4 × uint8   (4 bytes)  — r, g, b, a
└── Rotation:   4 × uint8   (4 bytes)  — qw, qx, qy, qz (packed)
```

**Script:** `ply_to_splat.py`

```bash
python ply_to_splat.py input.ply output.splat
```

### Color Conversion (SH → RGB)
```python
SH_C0 = 0.28209479177387814
r = clamp((0.5 + SH_C0 * f_dc_0) * 255, 0, 255)
g = clamp((0.5 + SH_C0 * f_dc_1) * 255, 0, 255)
b = clamp((0.5 + SH_C0 * f_dc_2) * 255, 0, 255)
```

### Opacity Pruning

Remove near-transparent Gaussians to reduce file size:

```python
# Sigmoid to convert logit opacity → actual opacity
opacity = 1 / (1 + exp(-raw_opacity))

# Prune: keep only Gaussians with opacity > threshold
# threshold=0.05: 6.2M → 3.65M Gaussians (111 MB .splat)
# threshold=0.10: 6.2M → 3.22M Gaussians (98 MB .splat)
```

### File Sizes

| Format | Gaussians | Size | Notes |
|--------|-----------|------|-------|
| PLY (raw) | 6,198,455 | ~400 MB | Full precision, all SH bands |
| PLY (pruned) | 3,223,059 | ~200 MB | Opacity > 0.1 |
| .splat (pruned 0.05) | 3,651,666 | 111 MB | 32 bytes/Gaussian |
| .splat (pruned 0.10) | 3,223,059 | 98 MB | Best size/quality tradeoff |

---

## Step 7: Web Viewer Deployment

**Library:** [`@mkkellogg/gaussian-splats-3d`](https://github.com/mkkellogg/GaussianSplats3D)  
**Framework:** Vite  
**Hosting:** Vercel (static site, free tier)

### Viewer Code

**`index.html`:**
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>3D Gaussian Splat Viewer</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #000; overflow: hidden; }
        #viewer { width: 100vw; height: 100vh; }
        #progress {
            position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
            color: #fff; font: 24px monospace; z-index: 10;
        }
    </style>
</head>
<body>
    <div id="progress">Loading… <span id="pct">0</span>%</div>
    <div id="viewer"></div>
    <script type="module" src="/main.js"></script>
</body>
</html>
```

**`main.js`:**
```javascript
import * as GaussianSplats3D from '@mkkellogg/gaussian-splats-3d';

const progressEl = document.getElementById('progress');
const pctEl = document.getElementById('pct');
const viewerEl = document.getElementById('viewer');

const viewer = new GaussianSplats3D.Viewer({
    cameraUp: [0, -1, 0],
    initialCameraPosition: [0, -3, -3],
    initialCameraLookAt: [0, 0, 1],
    sharedMemoryForWorkers: false,
    selfDrivenMode: true,
    rootElement: viewerEl,
});

viewer.addSplatScene('./bottle_cuda.splat', {
    splatAlphaRemovalThreshold: 20,
    showLoadingUI: false,
    progressiveLoad: true,
    onProgress: (p) => {
        if (pctEl) pctEl.textContent = Math.round(p) + '%';
    }
}).then(() => {
    if (progressEl) progressEl.style.display = 'none';
    viewer.start();
});
```

### Deployment

```bash
# Build
npm run build

# Deploy to Vercel
npx vercel --prod
```

### Hosting Constraints
- **Vercel free tier:** 100MB max file size ✅ (our .splat is 98MB)
- **Vercel serverless functions:** ~4.5MB response limit ❌ (can't proxy large files)
- **GitHub Releases:** No CORS headers ❌ (can't fetch from browser)
- **Solution:** Host `.splat` files directly in Vercel's `public/` directory

---

## Data Formats Reference

### COLMAP Directory Structure (expected by gsplat)
```
project/
├── images/              # RGB images
│   ├── frame_00000.jpg
│   └── ...
├── images_2/            # 2x downscaled (if using --data_factor 2)
│   ├── frame_00000.jpg
│   └── ...
└── sparse/
    └── 0/
        ├── cameras.bin   # Camera intrinsics
        ├── images.bin    # Camera poses (world-to-camera)
        └── points3D.bin  # Sparse 3D point cloud
```

### COLMAP Camera Models

We use `PINHOLE` (4 params: fx, fy, cx, cy). No distortion — ARKit already undistorts.

```
PINHOLE: fx, fy, cx, cy
SIMPLE_PINHOLE: f, cx, cy (shared focal length)
OPENCV: fx, fy, cx, cy, k1, k2, p1, p2
```

### 3DGS PLY Format (Standard)

```
ply
format binary_little_endian 1.0
element vertex <count>
property float x
property float y
property float z
property float nx
property float ny
property float nz
property float f_dc_0          # SH band 0 (DC color), R
property float f_dc_1          # SH band 0, G
property float f_dc_2          # SH band 0, B
property float f_rest_0        # SH bands 1-3 (45 coefficients for sh_degree=3)
...
property float f_rest_44
property float opacity          # Logit-space (apply sigmoid for actual opacity)
property float scale_0          # Log-space (apply exp for actual scale)
property float scale_1
property float scale_2
property float rot_0            # Quaternion w
property float rot_1            # Quaternion x
property float rot_2            # Quaternion y
property float rot_3            # Quaternion z
end_header
```

### .splat Binary Format (Web Viewer)

Compact format: 32 bytes per Gaussian, no header.

```
Byte offset  | Type      | Field
0-3          | float32   | x position
4-7          | float32   | y position
8-11         | float32   | z position
12-15        | float32   | scale x (exp of log-scale)
16-19        | float32   | scale y
20-23        | float32   | scale z
24           | uint8     | red (0-255)
25           | uint8     | green
26           | uint8     | blue
27           | uint8     | alpha (sigmoid of logit opacity × 255)
28           | uint8     | quaternion w (packed: value × 128 + 128)
29           | uint8     | quaternion x
30           | uint8     | quaternion y
31           | uint8     | quaternion z
```

---

## Lessons Learned & Gotchas

### 1. ARKit Poses are Row-Major
The `cameraPoseARFrame` array in 3D Scanner App JSON is stored **row-major** (`reshape(4,4, order='C')`). Using column-major produces completely wrong camera positions.

### 2. Metal/MPS GPU Training Produces Garbage
OpenSplat with Metal acceleration generates 99%+ near-transparent Gaussians with spikey artifacts. Tested extensively with multiple configurations (5k-10k steps, alpha reset on/off, downscale 2/4, densification tuning). **CUDA is required for training.** The viewer is not the problem — CUDA-trained splats render perfectly in the same viewer.

### 3. gsplat Version Mismatch
The `main` branch of gsplat's examples requires features not in the pip-installed v1.5.3 (e.g., `gsplat.color_correct`). **Always checkout the tag matching your installed version:**
```bash
git checkout v1.5.3
```

### 4. `--data_factor` Requires Pre-Downscaled Images
gsplat does NOT auto-downscale. If you use `--data_factor 2`, you must create `images_2/` manually with images at half resolution.

### 5. Vite Strips Inline Module Scripts
Vite removes inline `<script type="module">` tags from HTML during build. Use external `.js` files instead.

### 6. Module Scripts are Deferred
`<script type="module">` is deferred by default — `DOMContentLoaded` has already fired by execution time. Don't wrap module code in a DOMContentLoaded listener.

### 7. GitHub Releases Has No CORS
You cannot `fetch()` files from GitHub Releases in the browser — CORS policy blocks it. Host assets in Vercel's `public/` directory or use a CDN with proper CORS headers.

### 8. Vercel Serverless Has ~4.5MB Response Limit
Can't proxy large files through Vercel serverless functions. Serve .splat files as static assets from `public/`.

### 9. Full SfM > ARKit Pose Triangulation
Running COLMAP's full `mapper` (SfM from scratch) produces better results than using ARKit poses with `point_triangulator`. The bundle adjustment refines poses beyond ARKit's accuracy.

### 10. Terminate Cloud GPUs Immediately
Cloud GPU billing is per-second. Download results and terminate the pod in the same session. An RTX A5000 at $0.16/hr is cheap, but forgetting to terminate adds up.

---

## Automation Architecture (SvelteKit + Firebase)

> For automating this pipeline as a web service.

### Proposed Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     SvelteKit Frontend                   │
│                   (Firebase Hosting)                     │
│                                                         │
│  ┌─────────┐    ┌──────────┐    ┌────────────────────┐ │
│  │ Upload  │───▶│ Progress │───▶│ 3D Viewer          │ │
│  │ Page    │    │ Tracker  │    │ (gaussian-splats-3d)│ │
│  └─────────┘    └──────────┘    └────────────────────┘ │
└───────┬─────────────┬───────────────────────────────────┘
        │             │ (Firestore realtime)
        ▼             ▼
┌──────────────────────────────────────────────┐
│           Firebase Backend                    │
│                                              │
│  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Cloud        │  │ Firestore            │ │
│  │ Storage      │  │ - jobs/{id}/status   │ │
│  │ (uploads +   │  │ - jobs/{id}/progress │ │
│  │  results)    │  │ - jobs/{id}/result   │ │
│  └──────┬───────┘  └──────────────────────┘ │
│         │                                    │
│  ┌──────▼───────┐                           │
│  │ Cloud        │                           │
│  │ Function     │  (triggered on upload)    │
│  │ (orchestrator)│                          │
│  └──────┬───────┘                           │
└─────────┼────────────────────────────────────┘
          │
          ▼  (API call)
┌──────────────────────────────────┐
│     GPU Worker (RunPod / Modal)  │
│                                  │
│  1. Download images from Storage │
│  2. Run COLMAP SfM               │
│  3. Train gsplat (30k steps)     │
│  4. Convert to .splat            │
│  5. Upload to Cloud Storage      │
│  6. Update Firestore status      │
│  7. Self-terminate               │
└──────────────────────────────────┘
```

### Processing Steps (Automated)

| Step | Where | Time | Notes |
|------|-------|------|-------|
| Upload ZIP | Client → Firebase Storage | ~10s | 86MB scan export |
| Extract & validate | Cloud Function | ~5s | Verify JSON+JPG structure |
| Spin up GPU worker | Cloud Function → RunPod API | ~30s | RTX A5000, $0.16/hr |
| ARKit → COLMAP | GPU worker | ~5s | `arkit_to_colmap.py` |
| COLMAP SfM | GPU worker | ~2 min | Feature extraction + matching + mapping |
| gsplat training | GPU worker | ~45 min | 30k steps on RTX A5000 |
| PLY → .splat | GPU worker | ~30s | Prune + compress |
| Upload result | GPU worker → Firebase Storage | ~10s | 98MB .splat |
| Terminate pod | GPU worker (self) | immediate | Stop billing |
| **Total** | | **~50 min** | **~$0.15** |

### Key API Endpoints Needed

```
POST /api/jobs          → Create processing job (upload ZIP)
GET  /api/jobs/:id      → Get job status + progress
GET  /api/splats/:id    → Get .splat download URL (signed)
```

### Firestore Schema

```typescript
interface Job {
    id: string;
    status: 'uploading' | 'queued' | 'processing' | 'training' | 'done' | 'error';
    progress: number;           // 0-100
    step: string;               // Current pipeline step
    uploadPath: string;         // Firebase Storage path
    resultPath?: string;        // .splat file path
    resultUrl?: string;         // Signed download URL
    gaussianCount?: number;     // Final Gaussian count
    trainingSteps?: number;     // Steps completed
    cost?: number;              // GPU cost in dollars
    createdAt: Timestamp;
    updatedAt: Timestamp;
    error?: string;
}
```

### Environment Requirements (GPU Worker)

```
- NVIDIA GPU with CUDA (≥16GB VRAM recommended)
- Python 3.10+
- COLMAP (apt install colmap)
- gsplat 1.5.3
- PyTorch 2.x with CUDA
- ImageMagick (for image downscaling)
- Firebase Admin SDK (for status updates)
```

---

## Quick Reference: Full Pipeline Commands

```bash
# 1. Convert ARKit export to COLMAP format
python arkit_to_colmap.py scan_data/2026_01_13_14_47_59/ -o output_colmap

# 2. Run COLMAP SfM
./run_colmap.sh output_colmap

# 3. Create downscaled images for training
mkdir output_colmap/images_2
for f in output_colmap/images/*.jpg; do
    convert "$f" -resize 50% "output_colmap/images_2/$(basename $f)"
done

# 4. Train on CUDA GPU (RunPod/cloud)
python simple_trainer.py default \
    --data_dir /workspace/output_colmap \
    --data_factor 2 \
    --result_dir /workspace/results \
    --max_steps 30000

# 5. Convert PLY to .splat
python ply_to_splat.py results/output.ply scene.splat

# 6. Deploy viewer
cp scene.splat splat-viewer/public/
cd splat-viewer && npx vercel --prod
```
