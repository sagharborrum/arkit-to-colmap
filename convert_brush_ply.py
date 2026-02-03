#!/usr/bin/env python3
"""Convert Brush PLY format to standard 3DGS PLY format."""

import struct
import sys

def convert_brush_to_standard(input_path, output_path):
    with open(input_path, 'rb') as f:
        # Read header
        header_lines = []
        properties = []
        vertex_count = 0
        
        while True:
            line = f.readline().decode('utf-8').strip()
            header_lines.append(line)
            
            if line.startswith('element vertex'):
                vertex_count = int(line.split()[-1])
            elif line.startswith('property float'):
                prop_name = line.split()[-1]
                properties.append(prop_name)
            elif line == 'end_header':
                break
        
        print(f"Found {vertex_count} vertices with {len(properties)} properties")
        print(f"Properties: {properties[:5]}...{properties[-5:]}")
        
        # Find property indices
        prop_indices = {name: i for i, name in enumerate(properties)}
        
        # Read all vertex data
        float_size = 4
        vertex_size = len(properties) * float_size
        vertex_data = f.read()
        
        if len(vertex_data) != vertex_count * vertex_size:
            print(f"Warning: Expected {vertex_count * vertex_size} bytes, got {len(vertex_data)}")
    
    # Define standard 3DGS property order
    standard_order = ['x', 'y', 'z', 'nx', 'ny', 'nz']
    standard_order += ['f_dc_0', 'f_dc_1', 'f_dc_2']
    standard_order += [f'f_rest_{i}' for i in range(45)]
    standard_order += ['opacity']
    standard_order += ['scale_0', 'scale_1', 'scale_2']
    standard_order += ['rot_0', 'rot_1', 'rot_2', 'rot_3']
    
    # Check which properties we have
    available = []
    missing = []
    for prop in standard_order:
        if prop in prop_indices:
            available.append(prop)
        else:
            missing.append(prop)
    
    print(f"Available: {len(available)}, Missing: {len(missing)}")
    if missing:
        print(f"Missing properties: {missing[:10]}...")
    
    # Build reorder mapping
    # For missing properties, we'll output 0
    reorder = []
    for prop in standard_order:
        if prop in prop_indices:
            reorder.append(prop_indices[prop])
        else:
            reorder.append(None)  # Will output 0
    
    # Write output
    with open(output_path, 'wb') as f:
        # Write header
        f.write(b'ply\n')
        f.write(b'format binary_little_endian 1.0\n')
        f.write(f'element vertex {vertex_count}\n'.encode())
        for prop in standard_order:
            f.write(f'property float {prop}\n'.encode())
        f.write(b'end_header\n')
        
        # Write reordered vertex data
        for i in range(vertex_count):
            offset = i * vertex_size
            for idx in reorder:
                if idx is not None:
                    val = struct.unpack_from('<f', vertex_data, offset + idx * float_size)[0]
                else:
                    val = 0.0
                f.write(struct.pack('<f', val))
            
            if (i + 1) % 10000 == 0:
                print(f"Processed {i + 1}/{vertex_count} vertices...")
    
    print(f"Wrote {output_path}")

if __name__ == '__main__':
    input_file = sys.argv[1] if len(sys.argv) > 1 else 'export_7000.ply'
    output_file = sys.argv[2] if len(sys.argv) > 2 else 'standard.ply'
    convert_brush_to_standard(input_file, output_file)
