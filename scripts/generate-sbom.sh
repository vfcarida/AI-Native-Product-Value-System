#!/usr/bin/env bash
# Generate Software Bill of Materials (SBOM) in CycloneDX, SPDX, table, and JSON formats (PROD-005)
# Usage: ./scripts/generate-sbom.sh [image-name] [output-dir]

set -euo pipefail

IMAGE_NAME="${1:-product-value-system:latest}"
OUTPUT_DIR="${2:-sbom}"

echo "Generating SBOM for ${IMAGE_NAME}"
mkdir -p "${OUTPUT_DIR}"

if ! command -v trivy >/dev/null 2>&1; then
    echo "Error: trivy CLI not installed on PATH" >&2
    exit 1
fi

echo "[1/4] Generating CycloneDX SBOM..."
trivy image --format cyclonedx \
    --output "${OUTPUT_DIR}/sbom-cyclonedx.json" \
    "${IMAGE_NAME}"

echo "[2/4] Generating SPDX SBOM..."
if command -v syft >/dev/null 2>&1; then
    syft "${IMAGE_NAME}" -o spdx-json > "${OUTPUT_DIR}/sbom-spdx.json"
else
    trivy image --format spdx-json \
        --output "${OUTPUT_DIR}/sbom-spdx.json" \
        "${IMAGE_NAME}"
fi

echo "[3/4] Generating human-readable table..."
trivy image --format table \
    --output "${OUTPUT_DIR}/sbom-table.txt" \
    "${IMAGE_NAME}"

echo "[4/4] Generating JSON vulnerability/package summary..."
trivy image --format json \
    --output "${OUTPUT_DIR}/sbom-summary.json" \
    "${IMAGE_NAME}"

echo ""
echo "SBOM generation complete -> ${OUTPUT_DIR}"
ls -lh "${OUTPUT_DIR}"
