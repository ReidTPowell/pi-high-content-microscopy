#!/usr/bin/env python3
"""
OpenPhenoME Integration - Embed HCS.ai Raw Data

Integrates with Recursion Pharma's OpenPhenoME model (recursionpharma/OpenPhenom)
to extract biological embeddings from HCSai raw TIFF microscopy images.

The model weights are not bundled. Confirm the model's separate license before use.

Collaborates with hcsai-reader by using the same filename parsing conventions:
  <prefix>_t<tp>_<row><col>_<site>_w<channel>_z<slice>.tif

Multi-channel handling: channels w0, w1 (etc.) from the same well/site/timepoint
are combined into a single multi-channel tensor before passing through OpenPhenoME.
The model is channel-agnostic and accepts 1-11 input channels.

Usage:
    embed_hcsai.py <sub-task> --path=<directory_or_file_path> [options]

Sub-tasks:
    extract-embeddings - Process images from an HCSai directory, optionally filtered by well/site
    embed-well-plate   - Featurize all wells in a plate and output structured results
    embed-image-file   - Process a single TIFF file directly

Options:
    --well=<A01>          Filter to specific well (for extract-embeddings / embed-well-plate)
    --site=<s0>           Filter to specific site/field (e.g., s0, s1)
    --channelwise         Return per-channel embeddings instead of pooled (384xC dims)
    --batch-size=N        Batch size for model inference (default: 8)
    --format=text|json    Output format (default: text)
    --output=<file.csv>   Save well-plate results to CSV file (for embed-well-plate)
"""

import argparse
import importlib.util
import csv
import json
import os
import re
import sys
from pathlib import Path

# Check if we're in the right environment
try:
    from PIL import Image
    import numpy as np
except ImportError:
    print("Error: This script requires the 'openphenom' conda environment.", file=sys.stderr)
    print("Activate it with: source /opt/anaconda3/etc/profile.d/conda.sh && conda activate openphenom", file=sys.stderr)
    sys.exit(1)


# ============================================================
# HCSai Filename Parsing (mirrors hcsai-reader conventions)
# ============================================================

HCSAI_FILENAME_PATTERN = re.compile(
    r'^(.+)_t(\d+)_(?P<row>[A-Z]+)(?P<col>\d+)_(?P<site>s\d+)_w(?P<channel>\d+)_z(?P<z>\d+)\.(?:tif|tiff)$',
    re.IGNORECASE,
)


def parse_hcsai_filename(filename):
    """
    Parse an HCSai TIFF filename to extract metadata.

    Expected pattern: <prefix>_t<tp>_<row><col>_<site>_w<channel>_z<slice>.tif
    Example: 10X_Dapi_Phalloidin_t0_A01_s0_w0_z0.tif

    Returns dict with keys: prefix, timepoint, well, row, col, site, channel, z_slice
    or None if the filename doesn't match.
    """
    name = os.path.basename(filename)
    match = HCSAI_FILENAME_PATTERN.match(name)
    if not match:
        return None

    row_letter = match.group('row').upper()
    col_num = int(match.group('col'))
    well_str = f"{row_letter}{col_num:02d}"

    return {
        'prefix': match.group(1),
        'timepoint': int(match.group(2)),
        'well': well_str,
        'row': row_letter,
        'col': col_num,
        'site': match.group('site'),   # e.g., "s0"
        'channel': int(match.group('channel')),  # e.g., 0 for w0
        'z_slice': int(match.group('z')),         # z-plane index
    }


# ============================================================
# Image Loading & Preprocessing (for OpenPhenoME)
# ============================================================

def preprocess_image(image_path, target_size=256):
    """
    Load and preprocess a TIFF image for OpenPhenoME.

    - Loads the image using PIL (handles multi-frame TIFFs where each frame = one channel)
    - Resizes to 256x256 pixels (OpenPhenoME's expected input size, matching ViT-S/16 patch grid)
    - Converts pixel values: uint8 stays as-is; uint16 is scaled down to uint8 by /256
    - Returns a numpy array of shape (C, H, W) with dtype uint8

    OpenPhenoME internally applies InstanceNorm after dividing pixels by 255.0.
    """
    try:
        img = Image.open(image_path)

        # Determine number of frames/channels in the TIFF
        n_frames = getattr(img, 'n_frames', 1) if hasattr(img, 'n_frames') else 1

        channels = []
        for frame_idx in range(n_frames):
            try:
                img.seek(frame_idx)
            except (EOFError, AttributeError):
                pass  # Single-frame image, seek not needed

            # Convert to grayscale ('L' mode). Handles 'I;16', 'RGB', etc.
            if img.mode in ('L', 'I', 'F'):
                frame = img.copy()
            else:
                frame = img.convert('L')

            arr = np.array(frame)

            # Handle different bit depths and convert to uint8 for the model
            if arr.dtype == np.uint8:
                pil_frame = Image.fromarray(arr, mode='L')
            elif arr.dtype in (np.uint16,):
                # Scale 16-bit down to 8-bit (OpenPhenoME normalizes by /255 internally)
                scaled = np.clip(arr.astype(np.float32) / 256.0, 0, 255).astype(np.uint8)
                pil_frame = Image.fromarray(scaled, mode='L')
            elif arr.dtype == np.int32 or arr.dtype == np.int16:
                # Signed integer types - normalize to uint8 range
                min_val = float(arr.min())
                max_val = float(arr.max())
                if max_val > min_val:
                    normalized = ((arr.astype(np.float32) - min_val) / (max_val - min_val)) * 255.0
                else:
                    normalized = np.zeros_like(arr, dtype=np.float32)
                pil_frame = Image.fromarray(normalized.astype(np.uint8), mode='L')
            elif arr.dtype == np.float32 or arr.dtype == np.float64:
                min_val = float(np.nanmin(arr))
                max_val = float(np.nanmax(arr))
                if max_val > min_val:
                    normalized = ((arr.astype(np.float32) - min_val) / (max_val - min_val)) * 255.0
                else:
                    normalized = np.zeros_like(arr, dtype=np.float32)
                pil_frame = Image.fromarray(normalized.astype(np.uint8), mode='L')
            else:
                # Fallback: try direct conversion
                pil_frame = frame.convert('L')

            # Resize to target_size x target_size using bilinear interpolation
            resized_pil = pil_frame.resize((target_size, target_size), Image.BILINEAR)
            resized_arr = np.array(resized_pil)  # shape (H, W), dtype uint8

            channels.append(resized_arr)

        if not channels:
            raise ValueError(f"No frames extracted from {image_path}")

        # Stack into (C, H, W) format - channel-first as expected by the model
        stacked = np.stack(channels, axis=0).astype(np.uint8)
        return stacked, n_frames

    except Exception as e:
        print(f"Warning: Error loading image {image_path}: {e}", file=sys.stderr)
        return None, 0


def preprocess_multichannel_image(channel_arrays):
    """
    Combine multiple preprocessed single-channel arrays into a multi-channel tensor.

    Args:
        channel_arrays: list of numpy arrays each with shape (1, H, W) or (H, W),
                        already resized to the target size (256x256).

    Returns:
        Combined array of shape (C, H, W) where C = number of channels.
    """
    processed_channels = []
    for arr in channel_arrays:
        if arr.ndim == 3 and arr.shape[0] == 1:
            # Remove singleton dimension -> (H, W)
            arr = arr[0]
        elif arr.ndim != 2:
            raise ValueError(f"Expected 2D array or (1,H,W), got shape {arr.shape}")
        processed_channels.append(arr)

    # Stack along channel dimension to get (C, H, W)
    combined = np.stack(processed_channels, axis=0).astype(np.uint8)
    return combined


# ============================================================
# OpenPhenoME Model Loading & Embedding Extraction
# ============================================================

def load_openphenome_model():
    """
    Load the OpenPhenoME model (recursionpharma/OpenPhenom) from HuggingFace Hub.

    The HF repo is a Python package with relative imports, so we use importlib
    to load each submodule individually within a synthetic package namespace.
    This resolves 'attempted relative import' errors when loading as flat files.

    Returns: (model_instance, repo_directory_path)
    """
    import torch
    from huggingface_hub import snapshot_download, hf_hub_download

    # Download model files to HF cache
    revision = os.environ.get("OPENPHENOM_REVISION") or None
    repo_dir = snapshot_download("recursionpharma/OpenPhenom", revision=revision)
    repo_str = str(repo_dir)

    # Create a synthetic package so relative imports in the model code resolve correctly
    import types
    pkg_name = "openphenome_model"

    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [repo_str]
        sys.modules[pkg_name] = pkg
    else:
        # Package already loaded - return cached model path info only
        pass

    def load_submodule(name):
        filepath = os.path.join(repo_str, name + '.py')
        full_name = f"{pkg_name}.{name}"
        if full_name in sys.modules:
            return sys.modules[full_name]
        spec = importlib.util.spec_from_file_location(full_name, filepath)
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = pkg_name
        sys.modules[full_name] = mod
        spec.loader.exec_module(mod)
        return mod

    # Load submodules in dependency order:
    # normalizer -> loss/masking/mae_utils/vit (independent) -> mae_modules (imports masking, vit)
    #   -> huggingface_mae (imports loss, normalizer, mae_modules, mae_utils, vit)
    load_submodule('normalizer')
    load_submodule('loss')
    load_submodule('masking')
    load_submodule('mae_utils')
    load_submodule('vit')
    load_submodule('mae_modules')
    huggingface_mae_mod = load_submodule('huggingface_mae')

    MAEModel = huggingface_mae_mod.MAEModel
    MAEConfig = huggingface_mae_mod.MAEConfig

    # Instantiate model from config and load weights.
    # NOTE: The HuggingFace repo stores the model as an xet checkpoint (model.safetensors
    # is actually a ZIP archive containing PyTorch serialization data). We need to
    # reconstruct it into a proper .pt file before torch.load can handle it.
    try:
        import torch
        import zipfile, tempfile, shutil

        config = MAEConfig.from_pretrained(repo_str)
        model = MAEModel(config)

        state_dict_path = os.path.join(repo_str, "model.safetensors")
        if not os.path.exists(state_dict_path):
            state_dict_path = hf_hub_download("recursionpharma/OpenPhenom", "model.safetensors", revision=revision)

        # Reconstruct the xet checkpoint zip as a proper PyTorch checkpoint file.
        # The original format is: model/data.pkl + model/data/N (tensor chunks).
        # We remap to archive/ prefix which torch.load expects for its new zipfile format.
        tmpdir = tempfile.mkdtemp()
        try:
            ckpt_zip_path = os.path.join(tmpdir, "model.pt")

            with zipfile.ZipFile(state_dict_path, 'r') as zf_in:
                with zipfile.ZipFile(ckpt_zip_path, 'w', zipfile.ZIP_STORED) as outzip:
                    for name in zf_in.namelist():
                        if not name.endswith('/'):
                            data_bytes = zf_in.read(name)
                            new_name = ('archive/' + name[len('model/'):]) \
                                         if name.startswith('model/') else 'archive/' + name
                            outzip.writestr(new_name, data_bytes)

            # Load the reconstructed checkpoint
            state_dict_raw = torch.load(ckpt_zip_path, map_location='cpu', weights_only=False)
            
            # Extract the actual state dict (the pickle stores it under 'state_dict' key)
            if isinstance(state_dict_raw, dict) and 'state_dict' in state_dict_raw:
                state_dict = state_dict_raw['state_dict']
            else:
                state_dict = state_dict_raw
        finally:
            shutil.rmtree(tmpdir)

        # Load weights into model (use strict=False to handle potential key name mismatches)
        missing_keys, unexpected_keys = model.load_state_dict(state_dict, strict=False)
        if missing_keys:
            print(f"Warning: {len(missing_keys)} keys not found in checkpoint", file=sys.stderr)
        if unexpected_keys:
            print(f"Warning: {len(unexpected_keys)} unexpected keys in checkpoint", file=sys.stderr)

        model.eval()

    except Exception as e:
        print(f"Error loading OpenPhenoME model weights: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    return model, repo_dir


def extract_embedding(model, image_array, channelwise=False):
    """
    Run the OpenPhenoME encoder on a preprocessed multi-channel image array.

    Args:
        model: MAEModel instance (loaded from OpenPhenoME)
        image_array: numpy array of shape (C, H, W), values 0-255 uint8
                     C can be 1 to 11 (channel-agnostic architecture)
        channelwise: if True, return per-channel embeddings (384×C dims);
                     else pooled embedding across all channels and patches (384 dims)

    Returns:
        numpy array of embeddings. Shape is (384,) for pooled or (384*C,) for channelwise.
    """
    import torch

    # Add batch dimension -> tensor shape (1, C, H, W), float as expected by model
    img_tensor = torch.from_numpy(image_array).float().unsqueeze(0)

    with torch.no_grad():
        model.return_channelwise_embeddings = channelwise
        embeddings = model.predict(img_tensor)

    return embeddings.squeeze(0).numpy()


# ============================================================
# HCSai Directory Scanning (integrates hcsai-reader conventions)
# ============================================================

def find_tif_files(directory, well_filter=None, site_filter=None):
    """
    Find all TIFF files in an HCSai raw data directory structure.

    Looks recursively for .tif/.tiff files matching the HCSai naming convention:
        <prefix>_t<tp>_<row><col>_<site>_w<channel>_z<slice>.tif

    Applies optional well/site filters based on parsed filename metadata.
    Skips companion statistics JSON files (which end with '.statistics.json' but are .json, not .tif).
    """
    dir_path = Path(str(directory))
    tif_files = []

    for root, _, filenames in os.walk(str(dir_path)):
        for filename in sorted(filenames):
            if not (filename.endswith('.tif') or filename.endswith('.tiff')):
                continue

            filepath = os.path.join(root, filename)
            parsed = parse_hcsai_filename(filepath)
            if not parsed:
                # Skip files that don't match the HCSai naming pattern
                continue

            # Apply well filter (e.g., "A01", "B05")
            if well_filter and parsed['well'].upper() != well_filter.upper():
                continue

            # Apply site filter (e.g., "s0", "s1")
            if site_filter and parsed['site'] != site_filter:
                continue

            tif_files.append(filepath)

    return sorted(tif_files)


def group_images_by_well_site_timepoint(tif_files):
    """
    Group TIFF files by (well, site, timepoint), collecting channels for multi-channel embedding.

    This is the core of HCSai integration: images with different channel indices
    (w0=DAPI, w1=TRITC, etc.) from the same well/site/timepoint are combined into
    a single multi-channel tensor before model inference.

    Returns dict: {(well, site, timepoint): [(channel_num, tif_path), ...]}
    Channels within each group are sorted numerically (w0 < w1 < w2...).
    """
    groups = {}

    for tif_file in tif_files:
        parsed = parse_hcsai_filename(tif_file)
        if not parsed:
            continue

        key = (parsed['well'], parsed['site'], parsed['timepoint'])
        if key not in groups:
            groups[key] = []

        groups[key].append((parsed['channel'], tif_file))

    # Sort channels within each group numerically
    for key in groups:
        groups[key].sort(key=lambda x: x[0])  # Sort by channel number (w0, w1, ...)

    return groups


# ============================================================
# Task Implementations
# ============================================================

def task_extract_embeddings(path, well=None, site=None, channelwise=False, batch_size=8, output_format="text"):
    """
    Extract embeddings from HCSai images in a directory.

    Groups multi-channel TIFFs by (well, site, timepoint) and runs OpenPhenoME
    on each group to produce embeddings. The model's channel-agnostic architecture
    allows combining any number of channels (1-11) into a single forward pass.
    """
    print("Loading OpenPhenoME model...", file=sys.stderr)
    model, repo_dir = load_openphenome_model()
    print(f"Model loaded successfully from {repo_dir}", file=sys.stderr)

    dir_path = Path(str(path))
    if not dir_path.exists():
        print(f"Error: Directory does not exist: {path}", file=sys.stderr)
        sys.exit(1)

    # Find TIFF files with optional filters (mirrors hcsai-reader conventions)
    tif_files = find_tif_files(dir_path, well_filter=well, site_filter=site)

    if not tif_files:
        print("No matching HCSai TIFF image files found.", file=sys.stderr)
        sys.exit(1)

    # Group by (well, site, timepoint) for multi-channel processing
    groups = group_images_by_well_site_timepoint(tif_files)

    results = []
    total_groups = len(groups)
    print(f"Found {len(tif_files)} TIFF files across {total_groups} well/site/timepoint combinations.", file=sys.stderr)

    if output_format == "json":
        print(json.dumps({"status": "processing", "total_groups": total_groups}), file=sys.stderr)

    for idx, ((well_name, site_num, tp), channel_list) in enumerate(sorted(groups.items())):
        # Process all channels together as a multi-channel image tensor
        combined_channels = []

        for ch_num, tif_path in channel_list:
            img_array, n_internal_chans = preprocess_image(tif_path)
            if img_array is not None:
                # Each .tif file may have multiple internal frames (rare), but typically 1 frame per w-channel.
                # We take the first frame from each file as that channel's contribution.
                combined_channels.append(img_array[0])

        if not combined_channels:
            print(f"Warning: No valid image data for {well_name}/{site_num}/t{tp}", file=sys.stderr)
            continue

        # Stack into multi-channel array (C, H, W) - this is the key integration point.
        # OpenPhenoME's channel-agnostic MAE processes all channels together,
        # producing contextualized representations that account for cross-channel relationships.
        multi_channel_img = preprocess_multichannel_image(combined_channels)
        actual_num_channels = len(combined_channels)

        try:
            embedding = extract_embedding(model, multi_channel_img, channelwise=channelwise)
            emb_list = embedding.tolist() if hasattr(embedding, 'tolist') else list(embedding)
            emb_dim = len(emb_list)

            result_entry = {
                'well': well_name,
                'row': well_name[0],
                'col': int(well_name[1:]) if len(well_name) > 1 else 0,
                'site': site_num,
                'timepoint': tp,
                'channels_processed': actual_num_channels,
                'channel_files': [os.path.basename(p) for _, p in channel_list],
                'channel_indices': [ch for ch, _ in channel_list],
                'embedding_dim': emb_dim,
                'embedding': emb_list,
            }

            results.append(result_entry)

            print(f"  [{idx+1}/{total_groups}] {well_name}/{site_num}/t{tp} "
                  f"(channels: {[ch for ch,_ in channel_list]}) -> dim={emb_dim}", file=sys.stderr)

        except Exception as e:
            print(f"Error processing {well_name}/{site_num}/t{tp}: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

    # Output results
    if output_format == "json":
        output = {
            "model": "recursionpharma/OpenPhenom",
            "total_groups_processed": len(results),
            "channelwise": channelwise,
            "results": results,
        }
        print(json.dumps(output, indent=2))
    else:
        # Text output - human-readable summary table + embedding previews
        lines = []
        emb_dim = results[0]['embedding_dim'] if results else 384
        mode_str = f"{emb_dim} (channelwise)" if channelwise else str(emb_dim)

        lines.append("OpenPhenoME Embeddings - Processed {} image groups".format(len(results)))
        lines.append("=" * 70)
        lines.append(f"Model: recursionpharma/OpenPhenom (CA-MAE ViT-S/16)")
        lines.append(f"Embedding dimension: {mode_str}")
        if well:
            lines.append(f"Well filter: {well}")
        if site:
            lines.append(f"Site filter: {site}")
        lines.append("")

        for r in results:
            emb_preview = " ".join([f"E_{i}={v:.3f}" for i, v in enumerate(r['embedding'][:5])]) + "..."
            chans_str = ", ".join(str(ch) for ch in r.get('channel_indices', []))
            lines.append(f"Well {r['well']} | Site {r['site']} | TP t{r['timepoint']} | Channels w[{chans_str}]")
            lines.append(f"  Files: {', '.join(r['channel_files'])}")
            lines.append(f"  Embedding [{emb_preview}]")

        print("\n".join(lines))


def task_embed_well_plate(path, channelwise=False, batch_size=8, output_file=None):
    """
    Process all wells in a plate and generate structured embedding table.

    Iterates through ALL TIFF files (no filters) to produce embeddings for every
    well/site/timepoint combination. Multi-channel images are combined per the
    HCSai filename convention before model inference.
    """
    print("Loading OpenPhenoME model...", file=sys.stderr)
    model, repo_dir = load_openphenome_model()

    dir_path = Path(str(path))
    if not dir_path.exists():
        print(f"Error: Directory does not exist: {path}", file=sys.stderr)
        sys.exit(1)

    # Find all TIFF files (no filters for full plate processing)
    tif_files = find_tif_files(dir_path)

    if not tif_files:
        print("No HCSai TIFF image files found in directory.", file=sys.stderr)
        sys.exit(1)

    # Group by (well, site, timepoint) for multi-channel processing
    groups = group_images_by_well_site_timepoint(tif_files)

    results = []
    total_groups = len(groups)
    print(f"Processing {total_groups} well/site/timepoint combinations across all wells...", file=sys.stderr)

    # Determine embedding dimension early (from first result for display purposes)
    emb_dim_placeholder = 384 * max(1, len(list(groups.values())[0])) if groups and channelwise else 384

    for idx, ((well_name, site_num, tp), channel_list) in enumerate(sorted(groups.items())):
        combined_channels = []

        # Load each channel's image data separately (each .tif is one wavelength/channel)
        for ch_num, tif_path in channel_list:
            img_array, _ = preprocess_image(tif_path)
            if img_array is not None:
                combined_channels.append(img_array[0])  # Take first frame from each file

        if not combined_channels:
            continue

        # Combine all channels into a single multi-channel tensor (C, H, W).
        # OpenPhenoME's channel-agnostic MAE processes these together to capture
        # cross-channel contextual relationships in the biological sample.
        multi_channel_img = preprocess_multichannel_image(combined_channels)
        actual_num_channels = len(combined_channels)

        try:
            embedding = extract_embedding(model, multi_channel_img, channelwise=channelwise)
            emb_list = embedding.tolist() if hasattr(embedding, 'tolist') else list(embedding)

            row_data = {
                'Well': well_name,
                'Row': well_name[0],
                'Column': int(well_name[1:]) if len(well_name) > 1 else 0,
                'Site': site_num,
                'Timepoint': tp,
                'Channels': actual_num_channels,
            }

            # Add embedding dimensions as individual columns for CSV (first N dims shown explicitly)
            max_csv_dims = 50
            for i in range(min(len(emb_list), max_csv_dims)):
                row_data[f'E_{i}'] = round(float(emb_list[i]), 6)

            if len(emb_list) > max_csv_dims:
                remaining = np.array(emb_list[max_csv_dims:])
                row_data['E_rest_mean'] = round(float(np.mean(remaining)), 6)
                row_data['E_rest_std'] = round(float(np.std(remaining)), 6)

            # Store full embedding as JSON string for CSV compatibility
            row_data['_full_embedding_json'] = json.dumps(emb_list)
            results.append(row_data)

        except Exception as e:
            print(f"Error processing {well_name}/{site_num}/t{tp}: {e}", file=sys.stderr)

    # Write CSV output if requested
    if output_file and results:
        csv_path = Path(str(output_file))
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            for row in results:
                # Truncate very large embedding JSON fields to keep CSV manageable
                if '_full_embedding_json' in row and len(row['_full_embedding_json']) > 5000:
                    row_copy = dict(row)
                    row_copy['_full_embedding_json'] = '[truncated - too large for CSV]'
                    writer.writerow(row_copy)
                else:
                    writer.writerow(row)
        print(f"Results saved to {csv_path}", file=sys.stderr)

    # Print summary table (showing first 10 entries + stats)
    lines = []
    mode_str = f"{len(emb_list)} (channelwise)" if channelwise and results else "384 (pooled)"
    lines.append("OpenPhenoME Embedding Summary - Full Well Plate")
    lines.append("=" * 70)
    lines.append(f"Model: recursionpharma/OpenPhenom (CA-MAE ViT-S/16)")
    lines.append(f"Embedding dimension: {mode_str}")
    lines.append(f"Total wells/sites/timepoints processed: {len(results)}")

    if results:
        # Show well distribution
        unique_wells = sorted(set(r['Well'] for r in results))
        display_wells = ', '.join(unique_wells[:20])
        lines.append(f"Unique wells ({len(unique_wells)}): {display_wells}")
        if len(unique_wells) > 20:
            lines.append(f"... and {len(unique_wells) - 20} more")

        # Show sample embedding stats from first result
        sample_emb = json.loads(results[0]['_full_embedding_json'])
        arr = np.array(sample_emb[:50])  # First 50 dims for display
        lines.append(f"\nSample embedding (first well {results[0]['Well']}):")
        lines.append(f"  Mean: {np.mean(arr):.4f} | Std: {np.std(arr):.4f}")
        lines.append(f"  Min: {np.min(arr):.4f} | Max: {np.max(arr):.4f}")

        # Show table preview of first 10 entries (dimensions E_0-E_9 shown)
        lines.append("\nEmbedding table (first 10 entries, dims E_0-E_9 shown):")
        header = "Well | Site | TP  | Chs | " + " ".join(f"E_{i}" for i in range(10))
        lines.append(header)
        lines.append("-" * len(header))

        for r in results[:10]:
            e_vals = " ".join([f"{r[f'E_{i}']:.2f}" if f'E_{i}' in r else '--' for i in range(10)])
            lines.append(f"{r['Well']} | {r['Site']:>4s} | t{r['Timepoint']:<3d}|  {r['Channels']:2d} | {e_vals}")

    print("\n".join(lines))


def task_embed_single_file(path, channelwise=False):
    """Process a single TIFF file with OpenPhenoME."""
    model, repo_dir = load_openphenome_model()

    file_path = Path(str(path))
    if not file_path.exists():
        print(f"Error: File does not exist: {path}", file=sys.stderr)
        sys.exit(1)

    # Parse filename for HCSai metadata (if it matches the convention)
    parsed = parse_hcsai_filename(file_path.name)
    if parsed:
        well_info = f"Well={parsed['well']}, Site={parsed['site']}, Channel=w{parsed['channel']}"
    else:
        well_info = "Filename does not match HCSai convention - treating as single-channel image"

    img_array, n_chans = preprocess_image(file_path)
    if img_array is None:
        print("Error: Could not load image.", file=sys.stderr)
        sys.exit(1)

    # For a single file, we process whatever channels it contains internally.
    # If the TIFF has multiple frames (e.g., RGB), they'll be treated as separate channels.
    # img_array is already shape (C, H, W) from preprocess_image - pass directly to extract_embedding.
    embedding = extract_embedding(model, img_array, channelwise=channelwise)
    emb_list = embedding.tolist() if hasattr(embedding, 'tolist') else list(embedding)

    mode_str = f"{len(emb_list)} (channelwise)" if channelwise else "384 (pooled)"

    print(f"OpenPhenoME Embedding - Single Image")
    print("=" * 50)
    print(f"File: {file_path.name}")
    print(f"Metadata: {well_info}")
    print(f"Channels in image: {n_chans if n_chans > 0 else img_array.shape[0]}")
    print(f"Embedding dimension: {mode_str}")
    print()

    # Show embedding statistics
    arr = np.array(emb_list)
    print(f"Mean: {np.mean(arr):.4f} | Std: {np.std(arr):.4f}")
    print(f"Min:  {np.min(arr):.4f} | Max: {np.max(arr):.4f}")
    print()

    # Show first/last few values for inspection
    preview_size = min(10, len(emb_list))
    print("First {} embedding values:".format(preview_size))
    for i in range(preview_size):
        print(f"  E_{i}: {emb_list[i]:.6f}")

    if len(emb_list) > 20:
        print("\nLast 5 embedding values:")
        start = max(len(emb_list) - 5, preview_size)
        for i in range(start, len(emb_list)):
            print(f"  E_{i}: {emb_list[i]:.6f}")


# ============================================================
# Main Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="OpenPhenoME Integration - Extract biological embeddings from HCSai raw data "
                    "using recursionpharma/OpenPhenom CA-MAE model"
    )
    parser.add_argument("subtask", 
                        choices=["extract-embeddings", "embed-well-plate", "embed-image-file"],
                        help="Sub-task to perform")
    parser.add_argument("--path", required=True, type=str,
                        help="Path to HCSai raw data directory or single TIFF file")
    parser.add_argument("--well", type=str, default=None,
                        help="Filter by well identifier (e.g., A01) - for extract-embeddings/embed-well-plate")
    parser.add_argument("--site", type=str, default=None,
                        help="Filter to specific site/field (e.g., s0, s1) - for extract-embeddings")
    parser.add_argument("--channelwise", action="store_true",
                        help="Return per-channel embeddings instead of pooled (384xC dims)")
    parser.add_argument("--batch-size", type=int, default=8,
                        help="Batch size for model inference (default: 8)")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format: text or json")
    parser.add_argument("--output", type=str, default=None,
                        help="Save well-plate results to CSV file - for embed-well-plate")

    args = parser.parse_args()

    if args.subtask == "extract-embeddings":
        task_extract_embeddings(
            path=args.path, 
            well=args.well, 
            site=args.site, 
            channelwise=args.channelwise,
            batch_size=args.batch_size,
            output_format=args.format
        )
    elif args.subtask == "embed-well-plate":
        task_embed_well_plate(
            path=args.path,
            channelwise=args.channelwise,
            batch_size=args.batch_size,
            output_file=args.output
        )
    elif args.subtask == "embed-image-file":
        task_embed_single_file(path=args.path, channelwise=args.channelwise)


if __name__ == "__main__":
    main()
