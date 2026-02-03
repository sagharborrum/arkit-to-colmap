# Overnight Testing Plan - Gaussian Splat on Mac Mini M1
**Date**: 2026-02-02 21:45 EST
**Goal**: Get a working Gaussian splat render from ARKit scan data

## Current Status
- ✅ COLMAP reconstruction works (65k points, 1.5px error)
- ❌ Brush output has spikey artifacts (likely coordinate system issue)
- ❌ Web viewers can't parse Brush's PLY format

## Tools to Test (Priority Order)

### 1. OpenSplat (HIGH PRIORITY)
**Why**: Native Mac Metal support, takes COLMAP directly, production-grade
**Repo**: https://github.com/pierotofy/OpenSplat
**Install**: 
```bash
brew install cmake opencv pytorch
git clone https://github.com/pierotofy/OpenSplat
cd OpenSplat && mkdir build && cd build
cmake .. && make -j$(sysctl -n hw.ncpu)
```
**Run**:
```bash
./opensplat /path/to/colmap/project -o output.ply
```

### 2. gsplat (Python)
**Why**: Well-maintained, flexible, might have MPS/Metal backend
**Repo**: https://github.com/nerfstudio-project/gsplat
**Install**:
```bash
pip install gsplat
```
**Note**: Check if MPS (Apple Metal) backend is supported

### 3. Fix Brush Coordinate System
**Why**: We already have a trained splat, might just need pose fix
**Approach**:
- Investigate ARKit coordinate conventions
- Apply rotation matrix to convert coordinate systems
- Re-export with corrected poses

### 4. nerfstudio
**Why**: Full pipeline, well-documented
**Repo**: https://github.com/nerfstudio-project/nerfstudio
**Note**: Has `ns-train splatfacto` for Gaussian splatting

## Test Protocol

For each tool:
1. **Document** installation steps
2. **Test** with our COLMAP data (`output/sparse/0/`)
3. **Evaluate** output quality
4. **Record** any errors or issues
5. **Try fixes** if problems arise

## Success Criteria
- Splat renders without spikey artifacts
- Recognizable scene (bottle on desk)
- Works in SuperSplat or antimatter15 viewer

## Research Questions
- What coordinate system does ARKit use?
- What does COLMAP expect?
- How to convert between them?

## Files We're Working With
- **Images**: `output/images/` (45 frames, 1920x1440)
- **COLMAP sparse**: `output/sparse/0/`
- **ARKit data**: `scan_data/`
- **Previous splat**: `export_7000.ply` (Brush output, broken)

---
## Progress Log

### Attempt 1: OpenSplat
**Time**: 21:47 EST
**Status**: ✅ Building, 🔄 Training

**Installation**:
```bash
brew install cmake opencv  # Already had pytorch
cd ~/projects && git clone --recursive https://github.com/pierotofy/OpenSplat.git
cd OpenSplat && mkdir build && cd build
cmake -DCMAKE_PREFIX_PATH=/opt/homebrew/opt/pytorch/share/cmake .. && make -j8
```

**Notes**:
- Metal support requires **full Xcode** (not just Command Line Tools)
- Built CPU-only for now (no `metal` compiler in PATH)
- Metal build needs: `cmake -DGPU_RUNTIME=MPS ..`

**Running**:
```bash
cd ~/projects/arkit-to-colmap
~/projects/OpenSplat/build/opensplat output -n 2000 -o opensplat_2000.ply --cpu
```
- Started at 21:51 EST
- CPU mode is ~100x slower than GPU
- Progress: Step 125/2000 (6%)
- Loss: ~0.22-0.28 (stable)
- ETA: ~25-30 minutes

### Attempt 2: gsplat (Python)
**Time**: 21:51 EST
**Status**: ⏸️ Paused (library only, needs training script)

**Installation**: 
```bash
pip3 install gsplat --break-system-packages
```

**Notes**:
- gsplat is a low-level rendering library
- Full training needs `simple_trainer.py` from gsplat examples
- Many dependencies (fused_ssim, viser, nerfview, etc.)
- Would use MPS natively

### Attempt 3: nerfstudio
**Time**: 21:54 EST  
**Status**: ❌ Python 3.14 incompatible

**Notes**:
- Failed to build `av` package (Cython issue)
- Would need Python 3.12 or earlier

### Next Steps
1. Wait for OpenSplat CPU run to complete
2. Evaluate output in SuperSplat
3. If good: try more iterations
4. If still artifacts: install Xcode for Metal (26GB), try GPU mode

### Environment
- Python 3.14.2
- PyTorch 2.10.0 (MPS available)
- OpenCV 4.13.0
- cmake 4.2.3
- gsplat 1.5.3

