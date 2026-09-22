#!/bin/bash
# AWS S3 Direct Download for SRA Files
# Downloads SRR9134611 and SRR9134613 from AWS S3 public bucket
# No SSL certificate issues, no sra-tools required

set -e

# Configuration
OUTPUT_DIR="${1:-./sample_sra}"
S3_BASE_URL="https://sra-pub-run-odp.s3.amazonaws.com/sra"

# Sample IDs
SAMPLES=("SRR9134611" "SRR9134613")

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "==========================================="
echo "AWS S3 Direct SRA Download"
echo "==========================================="
echo "Output directory: $OUTPUT_DIR"
echo "Samples: ${SAMPLES[*]}"
echo ""

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Check available download tools
DOWNLOADER=""
if command -v aria2c &> /dev/null; then
    DOWNLOADER="aria2c"
    echo -e "${GREEN}✓${NC} Found aria2c (fast multi-threaded downloader)"
elif command -v wget &> /dev/null; then
    DOWNLOADER="wget"
    echo -e "${YELLOW}!${NC} Using wget (install aria2c for faster downloads)"
elif command -v curl &> /dev/null; then
    DOWNLOADER="curl"
    echo -e "${YELLOW}!${NC} Using curl (install aria2c or wget for better performance)"
else
    echo -e "${RED}✗${NC} Error: No download tool found (need wget, curl, or aria2c)"
    exit 1
fi

echo ""

# Function to download with aria2c
download_aria2c() {
    local url="$1"
    local output="$2"

    aria2c \
        --continue=true \
        --max-connection-per-server=8 \
        --split=8 \
        --min-split-size=10M \
        --dir="$(dirname "$output")" \
        --out="$(basename "$output")" \
        --file-allocation=none \
        --summary-interval=2 \
        "$url"
}

# Function to download with wget
download_wget() {
    local url="$1"
    local output="$2"

    wget \
        --continue \
        --progress=bar:force \
        --show-progress \
        --timeout=30 \
        --tries=5 \
        --output-document="$output" \
        "$url"
}

# Function to download with curl
download_curl() {
    local url="$1"
    local output="$2"

    curl \
        --continue-at - \
        --location \
        --progress-bar \
        --retry 5 \
        --retry-delay 3 \
        --output "$output" \
        "$url"
}

# Function to format bytes
format_bytes() {
    local bytes=$1
    if [ "$bytes" -ge 1073741824 ]; then
        echo "$(awk "BEGIN {printf \"%.2f GB\", $bytes/1073741824}")"
    elif [ "$bytes" -ge 1048576 ]; then
        echo "$(awk "BEGIN {printf \"%.2f MB\", $bytes/1048576}")"
    else
        echo "$(awk "BEGIN {printf \"%.2f KB\", $bytes/1024}")"
    fi
}

# Download each sample
TOTAL_SUCCESS=0
TOTAL_FAILED=0

for SRR in "${SAMPLES[@]}"; do
    echo "==========================================="
    echo -e "${BLUE}Downloading: $SRR${NC}"
    echo "==========================================="

    # Create sample directory
    SAMPLE_DIR="$OUTPUT_DIR/$SRR"
    mkdir -p "$SAMPLE_DIR"

    # Define paths
    S3_URL="$S3_BASE_URL/$SRR/$SRR"
    OUTPUT_FILE="$SAMPLE_DIR/$SRR.sra"

    echo "URL: $S3_URL"
    echo "Output: $OUTPUT_FILE"
    echo ""

    # Check if file already exists
    if [ -f "$OUTPUT_FILE" ]; then
        FILE_SIZE=$(stat -f%z "$OUTPUT_FILE" 2>/dev/null || stat -c%s "$OUTPUT_FILE" 2>/dev/null)
        if [ "$FILE_SIZE" -gt 1000000 ]; then
            echo -e "${GREEN}✓${NC} File already exists ($(format_bytes $FILE_SIZE))"
            echo "   Skip download or remove file to re-download"
            TOTAL_SUCCESS=$((TOTAL_SUCCESS+1))
            echo ""
            continue
        else
            echo -e "${YELLOW}!${NC} Incomplete file found, restarting download..."
            rm -f "$OUTPUT_FILE"
        fi
    fi

    # Download
    START_TIME=$(date +%s)
    SUCCESS=false

    case "$DOWNLOADER" in
        aria2c)
            if download_aria2c "$S3_URL" "$OUTPUT_FILE"; then
                SUCCESS=true
            fi
            ;;
        wget)
            if download_wget "$S3_URL" "$OUTPUT_FILE"; then
                SUCCESS=true
            fi
            ;;
        curl)
            if download_curl "$S3_URL" "$OUTPUT_FILE"; then
                SUCCESS=true
            fi
            ;;
    esac

    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))

    # Check result
    if [ "$SUCCESS" = true ] && [ -f "$OUTPUT_FILE" ]; then
        FILE_SIZE=$(stat -f%z "$OUTPUT_FILE" 2>/dev/null || stat -c%s "$OUTPUT_FILE" 2>/dev/null)

        if [ "$FILE_SIZE" -gt 1000000 ]; then
            echo ""
            echo -e "${GREEN}✓ Download successful${NC}"
            echo "  Size: $(format_bytes $FILE_SIZE)"
            echo "  Time: ${ELAPSED}s"

            # Calculate speed
            if [ "$ELAPSED" -gt 0 ]; then
                SPEED=$((FILE_SIZE / ELAPSED))
                echo "  Speed: $(format_bytes $SPEED)/s"
            fi

            TOTAL_SUCCESS=$((TOTAL_SUCCESS+1))
        else
            echo ""
            echo -e "${RED}✗ Download failed - file too small${NC}"
            TOTAL_FAILED=$((TOTAL_FAILED+1))
        fi
    else
        echo ""
        echo -e "${RED}✗ Download failed${NC}"
        TOTAL_FAILED=$((TOTAL_FAILED+1))
    fi

    echo ""
done

# Summary
echo "==========================================="
echo "Download Summary"
echo "==========================================="
echo -e "Successful: ${GREEN}$TOTAL_SUCCESS${NC}"
echo -e "Failed: ${RED}$TOTAL_FAILED${NC}"
echo ""

# List all downloaded files
if [ "$TOTAL_SUCCESS" -gt 0 ]; then
    echo "Downloaded files:"
    find "$OUTPUT_DIR" -name "*.sra" -type f -exec ls -lh {} \;
    echo ""

    echo "Total size:"
    du -sh "$OUTPUT_DIR"
    echo ""

    echo "==========================================="
    echo "Next Steps"
    echo "==========================================="
    echo "1. Verify downloads:"
    echo "   ls -lh $OUTPUT_DIR/*/*.sra"
    echo ""
    echo "2. Run SRR pipeline:"
    echo "   docker run --rm -it \\"
    echo "     -v \$(pwd)/$OUTPUT_DIR:/work/data/sra \\"
    echo "     -v \$(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml \\"
    echo "     -v \$(pwd)/ref:/ref \\"
    echo "     -v \$(pwd)/cellranger:/opt/cellranger \\"
    echo "     -v \$(pwd)/logs:/work/logs \\"
    echo "     -v \$(pwd)/results:/work/results \\"
    echo "     genoar-srr:step9"
fi

# Exit code
if [ "$TOTAL_FAILED" -eq 0 ]; then
    echo -e "${GREEN}All downloads completed successfully!${NC}"
    exit 0
else
    echo -e "${RED}Some downloads failed.${NC}"
    exit 1
fi
