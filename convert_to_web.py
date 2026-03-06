"""
convert_to_webp.py
==================
Converts a folder of images (PNG, JPG, JPEG, GIF, BMP, TIFF)
to WebP format and saves them into an output folder.

USAGE
-----
1. Install Pillow if you haven't:
       pip install Pillow

2. Run the script:
       python convert_to_webp.py

   By default it reads from  ./input_images/
   and writes to             ./output_webp/

3. To use different folders, pass them as arguments:
       python convert_to_webp.py --input path/to/images --output path/to/output

4. To control quality (1–100, default 85):
       python convert_to_webp.py --quality 90

OPTIONS
-------
  --input    SOURCE folder  (default: ./input_images)
  --output   OUTPUT folder  (default: ./output_webp)
  --quality  WebP quality   (default: 85)
  --lossless Use lossless WebP (best for logos/graphics with transparency)
"""

import os
import argparse
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is not installed. Run:  pip install Pillow")
    raise SystemExit(1)

SUPPORTED = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'}


def convert_folder(input_dir: Path, output_dir: Path, quality: int, lossless: bool):
    output_dir.mkdir(parents=True, exist_ok=True)

    files = [f for f in input_dir.iterdir() if f.suffix.lower() in SUPPORTED]

    if not files:
        print(f"No supported images found in: {input_dir}")
        return

    print(f"Found {len(files)} image(s) in '{input_dir}'")
    print(f"Saving WebP files to '{output_dir}'\n")

    success, skipped, failed = 0, 0, 0

    for f in files:
        out_path = output_dir / (f.stem + '.webp')

        # Skip if already WebP with same name
        if f.suffix.lower() == '.webp' and not (output_dir / f.name).exists():
            try:
                import shutil
                shutil.copy2(f, output_dir / f.name)
                print(f"  COPIED  {f.name}  (already WebP)")
                success += 1
                continue
            except Exception as e:
                print(f"  FAILED  {f.name}: {e}")
                failed += 1
                continue

        try:
            with Image.open(f) as img:
                # Preserve transparency for PNG/GIF
                if img.mode in ('RGBA', 'LA', 'P'):
                    img = img.convert('RGBA')
                    save_kwargs = {
                        'format': 'WEBP',
                        'lossless': lossless or True,  # always lossless for transparent images
                        'quality': quality,
                    }
                else:
                    img = img.convert('RGB')
                    save_kwargs = {
                        'format': 'WEBP',
                        'lossless': lossless,
                        'quality': quality,
                        'method': 6,  # best compression method
                    }

                img.save(out_path, **save_kwargs)

                original_kb = f.stat().st_size / 1024
                new_kb      = out_path.stat().st_size / 1024
                saving      = (1 - new_kb / original_kb) * 100 if original_kb > 0 else 0

                print(f"  OK  {f.name:40s}  {original_kb:7.1f} KB  →  {new_kb:7.1f} KB  ({saving:+.1f}%)")
                success += 1

        except Exception as e:
            print(f"  FAILED  {f.name}: {e}")
            failed += 1

    print(f"\n{'─'*60}")
    print(f"  Converted : {success}")
    print(f"  Failed    : {failed}")
    print(f"  Total     : {len(files)}")
    print(f"  Output    : {output_dir.resolve()}")


def main():
    parser = argparse.ArgumentParser(description='Batch convert images to WebP')
    parser.add_argument('--input',    default='input_images',  help='Source folder')
    parser.add_argument('--output',   default='output_webp',   help='Output folder')
    parser.add_argument('--quality',  type=int, default=90,    help='WebP quality 1-100')
    parser.add_argument('--lossless', action='store_true',     help='Use lossless WebP')
    args = parser.parse_args()

    input_dir  = Path(args.input)
    output_dir = Path(args.output)

    if not input_dir.exists():
        print(f"ERROR: Input folder does not exist: {input_dir}")
        raise SystemExit(1)

    convert_folder(input_dir, output_dir, args.quality, args.lossless)


if __name__ == '__main__':
    main()