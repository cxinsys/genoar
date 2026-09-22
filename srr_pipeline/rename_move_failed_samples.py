import os
import re
import shutil

def detect_failed_samples(base_dir="."):
    """Return list of SRR sample directories where cellranger failed (has cellranger_output/ but no outs/)"""
    failed = []
    all_dirs = [d for d in os.listdir(base_dir)
                if os.path.isdir(os.path.join(base_dir, d)) and re.match(r"^SRR\d+$", d)]
    for sample in all_dirs:
        sample_path = os.path.join(base_dir, sample)
        if os.path.isdir(os.path.join(sample_path, "cellranger_output")) and \
           not os.path.isdir(os.path.join(sample_path, "outs")):
            failed.append(sample)
    return failed

def swap_fastq_names(sample_dir, sample_id):
    """Swap R1 and R2 fastq.gz filenames in given sample directory"""
    r1 = os.path.join(sample_dir, f"{sample_id}_S1_R1_001.fastq.gz")
    r2 = os.path.join(sample_dir, f"{sample_id}_S1_R2_001.fastq.gz")

    if not (os.path.isfile(r1) and os.path.isfile(r2)):
        print(f"⚠️  {sample_id}: R1 or R2 fastq file not found, skipping rename.")
        return

    temp_r1 = r1 + ".temp_swap"
    os.rename(r1, temp_r1)
    os.rename(r2, r1)
    os.rename(temp_r1, r2)
    print(f"🔁 Swapped R1 <-> R2 in {sample_id}")

def move_failed_sample(sample, base_dir=".", target_dir="./retry_failed"):
    src = os.path.join(base_dir, sample)
    dst = os.path.join(target_dir, sample)
    os.makedirs(target_dir, exist_ok=True)

    # Delete cellranger_output/
    failed_output = os.path.join(src, "cellranger_output")
    if os.path.isdir(failed_output):
        shutil.rmtree(failed_output)
        print(f"🗑️  Removed cellranger_output/ from {sample}")

    if os.path.exists(dst):
        print(f"⚠️  {sample} already exists in {target_dir}, skipping move.")
        return False

    shutil.move(src, dst)
    print(f"📦 Moved {sample} → {target_dir}/")
    return True  # moved

def save_sample_list(sample_ids, output_path):
    with open(output_path, "w") as f:
        for sid in sample_ids:
            f.write(sid + "\n")
    print(f"\n📝 Saved moved sample list to: {output_path}")

def main(base_dir=".", retry_dir="./retry_failed"):
    failed_samples = detect_failed_samples(base_dir)
    if not failed_samples:
        print("✅ No failed samples found.")
        return

    print(f"🔍 Found {len(failed_samples)} failed samples. Processing...\n")

    moved_samples = []

    for sample in failed_samples:
        sample_path = os.path.join(base_dir, sample)
        swap_fastq_names(sample_path, sample)
        moved = move_failed_sample(sample, base_dir, retry_dir)
        if moved:
            moved_samples.append(sample)

    if moved_samples:
        save_sample_list(moved_samples, os.path.join(base_dir, "retry_failed_samples.txt"))
    else:
        print("⚠️  No samples were moved, nothing written to list.")

    print(f"\n✅ All failed samples processed and moved to {retry_dir}/")

if __name__ == "__main__":
    main()

