#!/bin/bash
# Run COLMAP pipeline on ARKit-converted data
# Usage: ./run_colmap.sh <project_dir>

set -e

PROJECT_DIR="${1:-.}"
DATABASE_PATH="$PROJECT_DIR/database.db"
IMAGE_PATH="$PROJECT_DIR/images"
SPARSE_PATH="$PROJECT_DIR/sparse/0"

echo "=== COLMAP Pipeline ==="
echo "Project: $PROJECT_DIR"
echo ""

# Check COLMAP is installed
if ! command -v colmap &> /dev/null; then
    echo "Error: COLMAP not found. Install with: brew install colmap"
    exit 1
fi

# Check required directories exist
if [ ! -d "$IMAGE_PATH" ]; then
    echo "Error: Images directory not found: $IMAGE_PATH"
    exit 1
fi

if [ ! -f "$SPARSE_PATH/cameras.txt" ]; then
    echo "Error: COLMAP model not found: $SPARSE_PATH/cameras.txt"
    echo "Run arkit_to_colmap.py first."
    exit 1
fi

# Remove old database if exists
rm -f "$DATABASE_PATH"

echo "=== Step 1: Feature Extraction ==="
colmap feature_extractor \
    --database_path "$DATABASE_PATH" \
    --image_path "$IMAGE_PATH" \
    --ImageReader.camera_model PINHOLE \
    --ImageReader.single_camera 1

echo ""
echo "=== Step 2: Sequential Matching ==="
colmap sequential_matcher \
    --database_path "$DATABASE_PATH" \
    --SequentialMatching.overlap 10

echo ""
echo "=== Step 3: Point Triangulation ==="
# Use ARKit poses to triangulate 3D points
colmap point_triangulator \
    --database_path "$DATABASE_PATH" \
    --image_path "$IMAGE_PATH" \
    --input_path "$SPARSE_PATH" \
    --output_path "$SPARSE_PATH"

echo ""
echo "=== Step 4: Model Statistics ==="
colmap model_analyzer \
    --path "$SPARSE_PATH"

echo ""
echo "=== Step 5: Convert to Binary (for gsplat) ==="
colmap model_converter \
    --input_path "$SPARSE_PATH" \
    --output_path "$SPARSE_PATH" \
    --output_type BIN

echo ""
echo "✅ COLMAP pipeline complete!"
echo "   Sparse model: $SPARSE_PATH"
echo "   Files: cameras.bin, images.bin, points3D.bin"
echo ""
echo "Next step: Train Gaussian splat with gsplat or upload to cloud GPU"
