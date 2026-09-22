import os
import re

# Directory to scan (the one holding the log files)
target_dir = "./"

# Find the .out files in that directory
out_files = [f for f in os.listdir(target_dir) if f.endswith('.out')]

success_srrs = set()
failed_srrs = set()

# Read each .out file
for file in out_files:
    file_path = os.path.join(target_dir, file)
    with open(file_path, 'r') as f:
        for line in f:
            # Success
            match_success = re.search(r'(SRR\d+) fastq-dump completed successfully', line)
            if match_success:
                success_srrs.add(match_success.group(1))
                continue

            # Failure
            match_error = re.search(r'Error processing (SRR\d+)', line)
            if match_error:
                failed_srrs.add(match_error.group(1))

# Print the results
print("SRRs where fastq-dump succeeded ({}):".format(len(success_srrs)))
for srr in sorted(success_srrs):
    print(srr)

print("\n SRRs where fastq-dump failed ({}):".format(len(failed_srrs)))
for srr in sorted(failed_srrs):
    print(srr)

# The result files are written to the current working directory (the home directory, say)
with open('fastq_success.txt', 'w') as f_success:
    for srr in sorted(success_srrs):
        f_success.write(srr + '\n')

with open('fastq_failed.txt', 'w') as f_failed:
    for srr in sorted(failed_srrs):
        f_failed.write(srr + '\n')

print("\n Results written to 'fastq_success.txt' and 'fastq_failed.txt' in the current directory.")

