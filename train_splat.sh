#!/bin/bash
# Train Gaussian splat using Brush (Mac M1+)
# Usage: ./train_splat.sh <colmap_output_dir> [steps]

set -e

INPUT_DIR="${1:-output}"
STEPS="${2:-7000}"

# Download Brush if not present
if [ ! -f "brush-app-aarch64-apple-darwin/brush_app" ]; then
    echo "Downloading Brush v0.3.0..."
    curl -L -o brush-app.tar.xz "https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/brush-app-aarch64-apple-darwin.tar.xz"
    xz -d brush-app.tar.xz
    tar -xf brush-app.tar
    rm brush-app.tar
    chmod +x brush-app-aarch64-apple-darwin/brush_app
fi

echo "=== Training Gaussian Splat ==="
echo "Input: $INPUT_DIR"
echo "Steps: $STEPS"
echo ""

./brush-app-aarch64-apple-darwin/brush_app "$INPUT_DIR" --with-viewer --total-steps "$STEPS"

echo ""
echo "✅ Training complete!"
echo "Output: export_${STEPS}.ply"
