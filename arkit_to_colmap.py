#!/usr/bin/env python3
"""
ARKit to COLMAP Converter
Converts 3D Scanner App exports to COLMAP sparse model format.

Based on Technical Specification: ARKit to COLMAP Pipeline for Mac Mini
"""

import argparse
import json
import os
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation
from tqdm import tqdm


def parse_arkit_json(json_path: Path) -> dict:
    """Parse 3D Scanner app JSON export."""
    with open(json_path) as f:
        data = json.load(f)
    
    # Parse 4x4 camera-to-world matrix
    # ARKit stores as flat 16-element array in ROW-MAJOR order
    pose_flat = data['cameraPoseARFrame']
    pose = np.array(pose_flat).reshape(4, 4, order='C')  # Row-major (C order)
    
    # Parse 3x3 intrinsics matrix (stored row-major as flat array)
    K_flat = data['intrinsics']
    K = np.array(K_flat).reshape(3, 3)
    
    return {
        'pose': pose,           # 4x4 camera-to-world
        'fx': K[0, 0],
        'fy': K[1, 1],
        'cx': K[0, 2],
        'cy': K[1, 2],
        'motion_quality': data.get('motionQuality', 1.0),
        'frame_index': data.get('frame_index', 0),
        'json_path': json_path,
    }


def arkit_to_colmap_pose(c2w_arkit: np.ndarray) -> tuple:
    """
    Convert ARKit camera-to-world to COLMAP world-to-camera.
    
    ARKit: Y-up, -Z forward, right-handed
    COLMAP: Y-down, Z forward, right-handed
    
    Returns: (qw, qx, qy, qz, tx, ty, tz)
    """
    # Coordinate system conversion: flip Y and Z axes
    flip = np.diag([1.0, -1.0, -1.0, 1.0])
    c2w_colmap = flip @ c2w_arkit @ flip
    
    # Invert to get world-to-camera (what COLMAP stores)
    w2c = np.linalg.inv(c2w_colmap)
    
    # Extract rotation and translation
    R = w2c[:3, :3]
    t = w2c[:3, 3]
    
    # Convert rotation to quaternion using scipy
    # scipy returns [qx, qy, qz, qw], COLMAP wants [qw, qx, qy, qz]
    quat = Rotation.from_matrix(R).as_quat()
    qw, qx, qy, qz = quat[3], quat[0], quat[1], quat[2]
    
    return qw, qx, qy, qz, t[0], t[1], t[2]


def find_matching_image(json_path: Path, scan_dir: Path) -> Path | None:
    """Find the JPG image matching a JSON metadata file."""
    # Try same name with .jpg extension
    frame_num = json_path.stem.replace('frame_', '')
    
    # Try frame_XXXXX.jpg
    jpg_path = scan_dir / f"frame_{frame_num}.jpg"
    if jpg_path.exists():
        return jpg_path
    
    # Try with different padding
    try:
        idx = int(frame_num)
        for fmt in ['frame_{:05d}.jpg', 'frame_{:04d}.jpg', 'frame_{:03d}.jpg']:
            jpg_path = scan_dir / fmt.format(idx)
            if jpg_path.exists():
                return jpg_path
    except ValueError:
        pass
    
    return None


def write_colmap_model(frames: list, output_dir: Path, image_width: int, image_height: int):
    """Write COLMAP sparse model files in text format."""
    sparse_dir = output_dir / 'sparse' / '0'
    sparse_dir.mkdir(parents=True, exist_ok=True)
    
    # Get average intrinsics (should be same for all frames from same device)
    fx = np.mean([f['fx'] for f in frames])
    fy = np.mean([f['fy'] for f in frames])
    cx = np.mean([f['cx'] for f in frames])
    cy = np.mean([f['cy'] for f in frames])
    
    # cameras.txt - Single shared camera (PINHOLE model)
    cameras_path = sparse_dir / 'cameras.txt'
    with open(cameras_path, 'w') as f:
        f.write("# Camera list with one line of data per camera:\n")
        f.write("# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n")
        f.write(f"1 PINHOLE {image_width} {image_height} {fx:.6f} {fy:.6f} {cx:.6f} {cy:.6f}\n")
    
    # images.txt - Two lines per image
    images_path = sparse_dir / 'images.txt'
    with open(images_path, 'w') as f:
        f.write("# Image list with two lines of data per image:\n")
        f.write("# IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n")
        f.write("# POINTS2D[] as (X, Y, POINT3D_ID)\n")
        
        for i, frame in enumerate(frames, 1):
            qw, qx, qy, qz, tx, ty, tz = arkit_to_colmap_pose(frame['pose'])
            filename = frame['image_name']
            f.write(f"{i} {qw:.10f} {qx:.10f} {qy:.10f} {qz:.10f} {tx:.10f} {ty:.10f} {tz:.10f} 1 {filename}\n")
            f.write("\n")  # Empty line for 2D points (populated by COLMAP)
    
    # points3D.txt - Initially empty
    points_path = sparse_dir / 'points3D.txt'
    with open(points_path, 'w') as f:
        f.write("# 3D point list with one line of data per point:\n")
        f.write("# POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)\n")
    
    print(f"  Written: {cameras_path}")
    print(f"  Written: {images_path}")
    print(f"  Written: {points_path}")


def process_scan(
    input_dir: Path,
    output_dir: Path,
    quality_threshold: float = 0.8,
    frame_skip: int = 1,
) -> dict:
    """
    Process an ARKit scan export and convert to COLMAP format.
    
    Args:
        input_dir: Path to scan export folder (containing .jpg and .json files)
        output_dir: Path to output directory
        quality_threshold: Minimum motionQuality to include frame (0-1)
        frame_skip: Process every Nth frame (1 = all frames)
    
    Returns:
        dict with processing statistics
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    
    # Find all JSON metadata files
    json_files = sorted(input_dir.glob('frame_*.json'))
    if not json_files:
        # Try nested structure
        json_files = sorted(input_dir.rglob('frame_*.json'))
    
    if not json_files:
        raise FileNotFoundError(f"No frame_*.json files found in {input_dir}")
    
    print(f"Found {len(json_files)} JSON metadata files")
    
    # Parse all frames
    frames = []
    skipped_quality = 0
    skipped_no_image = 0
    
    scan_dir = json_files[0].parent  # Directory containing the scan files
    
    for i, json_path in enumerate(tqdm(json_files, desc="Parsing metadata")):
        # Skip frames based on frame_skip
        if i % frame_skip != 0:
            continue
        
        try:
            frame = parse_arkit_json(json_path)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  Warning: Could not parse {json_path}: {e}")
            continue
        
        # Filter by quality
        if frame['motion_quality'] < quality_threshold:
            skipped_quality += 1
            continue
        
        # Find matching image
        image_path = find_matching_image(json_path, scan_dir)
        if image_path is None:
            skipped_no_image += 1
            continue
        
        frame['image_path'] = image_path
        frame['image_name'] = image_path.name
        frames.append(frame)
    
    print(f"\nFiltering results:")
    print(f"  Passed quality filter: {len(frames)}")
    print(f"  Skipped (low quality): {skipped_quality}")
    print(f"  Skipped (no image): {skipped_no_image}")
    
    if len(frames) < 10:
        raise ValueError(f"Only {len(frames)} frames passed filtering. Need at least 10.")
    
    # Get image dimensions from first image
    with Image.open(frames[0]['image_path']) as img:
        image_width, image_height = img.size
    print(f"  Image dimensions: {image_width}x{image_height}")
    
    # Create output directories
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / 'images'
    images_dir.mkdir(exist_ok=True)
    
    # Copy images to output
    print(f"\nCopying {len(frames)} images...")
    for frame in tqdm(frames, desc="Copying images"):
        dst = images_dir / frame['image_name']
        if not dst.exists():
            shutil.copy2(frame['image_path'], dst)
    
    # Write COLMAP model
    print("\nWriting COLMAP sparse model...")
    write_colmap_model(frames, output_dir, image_width, image_height)
    
    stats = {
        'total_frames': len(json_files),
        'processed_frames': len(frames),
        'skipped_quality': skipped_quality,
        'skipped_no_image': skipped_no_image,
        'image_width': image_width,
        'image_height': image_height,
        'quality_threshold': quality_threshold,
    }
    
    print(f"\n✅ COLMAP model created at: {output_dir}")
    print(f"   Frames: {len(frames)}")
    print(f"   Next step: Run COLMAP feature extraction and triangulation")
    
    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Convert ARKit 3D Scanner App export to COLMAP format'
    )
    parser.add_argument(
        'input',
        type=Path,
        help='Path to scan export folder (containing frame_*.jpg and frame_*.json)'
    )
    parser.add_argument(
        '-o', '--output',
        type=Path,
        default=None,
        help='Output directory (default: <input>_colmap)'
    )
    parser.add_argument(
        '-q', '--quality-threshold',
        type=float,
        default=0.8,
        help='Minimum motionQuality to include frame (default: 0.8)'
    )
    parser.add_argument(
        '--frame-skip',
        type=int,
        default=1,
        help='Process every Nth frame (default: 1 = all frames)'
    )
    
    args = parser.parse_args()
    
    if args.output is None:
        args.output = args.input.parent / f"{args.input.name}_colmap"
    
    stats = process_scan(
        args.input,
        args.output,
        quality_threshold=args.quality_threshold,
        frame_skip=args.frame_skip,
    )
    
    return stats


if __name__ == '__main__':
    main()
