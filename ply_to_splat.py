#!/usr/bin/env python3
"""Convert PLY to .splat format for web viewers."""

import struct
import numpy as np
from plyfile import PlyData

def sigmoid(x):
    return 1 / (1 + np.exp(-x))

def ply_to_splat(input_path, output_path):
    print(f"Loading {input_path}...")
    plydata = PlyData.read(input_path)
    vertex = plydata['vertex']
    
    print(f"Found {len(vertex)} vertices")
    print(f"Properties: {[p.name for p in vertex.properties]}")
    
    # Extract properties
    x = vertex['x']
    y = vertex['y'] 
    z = vertex['z']
    
    # DC color coefficients (SH0)
    f_dc_0 = vertex['f_dc_0']
    f_dc_1 = vertex['f_dc_1']
    f_dc_2 = vertex['f_dc_2']
    
    # Opacity (in log space, needs sigmoid)
    opacity = vertex['opacity']
    
    # Scale (in log space, needs exp)
    scale_0 = vertex['scale_0']
    scale_1 = vertex['scale_1']
    scale_2 = vertex['scale_2']
    
    # Rotation quaternion
    rot_0 = vertex['rot_0']
    rot_1 = vertex['rot_1']
    rot_2 = vertex['rot_2']
    rot_3 = vertex['rot_3']
    
    # Convert to displayable values
    # Color: SH coefficient to RGB (simplified - just use DC term)
    SH_C0 = 0.28209479177387814
    r = np.clip((0.5 + SH_C0 * f_dc_0) * 255, 0, 255).astype(np.uint8)
    g = np.clip((0.5 + SH_C0 * f_dc_1) * 255, 0, 255).astype(np.uint8)
    b = np.clip((0.5 + SH_C0 * f_dc_2) * 255, 0, 255).astype(np.uint8)
    
    # Alpha from opacity
    a = np.clip(sigmoid(opacity) * 255, 0, 255).astype(np.uint8)
    
    # Scale: exp to get actual scale, then normalize
    scale = np.exp(np.stack([scale_0, scale_1, scale_2], axis=-1))
    
    # Normalize quaternion
    quat = np.stack([rot_0, rot_1, rot_2, rot_3], axis=-1)
    quat = quat / np.linalg.norm(quat, axis=-1, keepdims=True)
    
    # Convert quaternion to packed format (128 = 0, 0-255 range)
    quat_packed = np.clip((quat * 128 + 128), 0, 255).astype(np.uint8)
    
    # Scale to packed format
    max_scale = np.max(scale)
    scale_normalized = scale / max_scale
    scale_packed = np.clip(scale_normalized * 255, 0, 255).astype(np.uint8)
    
    # Write .splat format
    # Format: for each splat: 3 floats (pos) + 3 floats (scale) + 4 bytes (rgba) + 4 bytes (quat)
    # = 12 + 12 + 4 + 4 = 32 bytes per splat
    
    print(f"Writing {output_path}...")
    with open(output_path, 'wb') as f:
        for i in range(len(vertex)):
            # Position (3 floats)
            f.write(struct.pack('<fff', x[i], y[i], z[i]))
            # Scale (3 floats) 
            f.write(struct.pack('<fff', scale[i, 0], scale[i, 1], scale[i, 2]))
            # Color RGBA (4 bytes)
            f.write(struct.pack('BBBB', r[i], g[i], b[i], a[i]))
            # Rotation quaternion (4 bytes)
            f.write(struct.pack('BBBB', quat_packed[i, 0], quat_packed[i, 1], 
                                        quat_packed[i, 2], quat_packed[i, 3]))
            
            if (i + 1) % 20000 == 0:
                print(f"  {i+1}/{len(vertex)}")
    
    print(f"Done! Wrote {len(vertex)} splats")
    
if __name__ == '__main__':
    import sys
    input_file = sys.argv[1] if len(sys.argv) > 1 else 'export_7000.ply'
    output_file = sys.argv[2] if len(sys.argv) > 2 else 'scene.splat'
    ply_to_splat(input_file, output_file)
