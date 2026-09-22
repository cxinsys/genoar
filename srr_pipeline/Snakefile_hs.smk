import os

# Derive the sample wildcards from the directory and file names
FASTQ_PATH = os.getcwd() + "/"
fastq_samples = glob_wildcards(FASTQ_PATH + "{sample_dir}/{sample_dir}_S1_R1_001.fastq.gz").sample_dir

# Global settings
CELLRANGER = "/path/to/cellranger-8.0.1/cellranger"
REFERENCE = "/path/to/refdata-gex-GRCh38-2024-A"
ERROR_LOG = FASTQ_PATH + "error_log.txt"

# Final targets
rule all:
    input:
        expand(FASTQ_PATH + "{sample_dir}/outs/possorted_genome_bam.bam", sample_dir=fastq_samples)

# Cell Ranger mapping rule, one per sample
rule cellranger_count:
    input:
        r1=FASTQ_PATH + "{sample_dir}/{sample_dir}_S1_R1_001.fastq.gz",
        r2=FASTQ_PATH + "{sample_dir}/{sample_dir}_S1_R2_001.fastq.gz"
    output:
        bam=FASTQ_PATH + "{sample_dir}/outs/possorted_genome_bam.bam"
    log:
        error = FASTQ_PATH + "{sample_dir}/logs/{sample_dir}.log"
    shell:
        """
        mkdir -p {FASTQ_PATH}{wildcards.sample_dir}/logs
        cd {FASTQ_PATH}{wildcards.sample_dir}

        {CELLRANGER} count \
            --id=cellranger_output \
            --transcriptome={REFERENCE} \
            --fastqs=. \
            --sample={wildcards.sample_dir} \
            --localcores=16 \
            --localmem=100 \
            --create-bam=true > {log.error} 2>&1

        # Move the outputs to where we want them
        mkdir -p outs
        mv cellranger_output/outs/* outs/
        rm -rf cellranger_output
        """

