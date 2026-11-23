#!/usr/bin/env bash
# Migrate local .env credentials to HashiCorp Vault KV v2 (PROD-004)
# Supports --dry-run to validate mappings without contacting a live Vault server.

set -euo pipefail

DRY_RUN=false
ENV_FILE=".env"
MOUNT_POINT="${VAULT_MOUNT_POINT:-secret}"
BASE_PATH="${VAULT_BASE_PATH:-product-value-system}"
VAULT_ADDR="${VAULT_ADDR:-https://vault.company.internal:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

echo "Migrating secrets to HashiCorp Vault"
echo "  Vault Addr : ${VAULT_ADDR}"
echo "  KV v2 Path : ${MOUNT_POINT}/${BASE_PATH}"
echo "  Env File   : ${ENV_FILE}"
echo "  Dry Run    : ${DRY_RUN}"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Error: ${ENV_FILE} not found" >&2
    exit 1
fi

if [[ "${DRY_RUN}" == "false" ]]; then
    if [[ -z "${VAULT_TOKEN}" ]]; then
        echo "Error: VAULT_TOKEN must be set (or pass --dry-run)" >&2
        exit 1
    fi
    if ! command -v vault >/dev/null 2>&1; then
        echo "Error: vault CLI not installed on PATH" >&2
        exit 1
    fi
    if ! vault status >/dev/null 2>&1; then
        echo "Error: cannot connect to Vault at ${VAULT_ADDR}" >&2
        exit 1
    fi
fi

# Safely load KEY=VALUE lines from env file (ignores comments/blank lines)
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

put_secret() {
    local sub_path="$1"
    shift
    if [[ "${DRY_RUN}" == "true" ]]; then
        echo "[DRY-RUN] vault kv put ${MOUNT_POINT}/${BASE_PATH}/${sub_path} (<redacted keys>)"
    else
        vault kv put "${MOUNT_POINT}/${BASE_PATH}/${sub_path}" "$@" >/dev/null
        echo "[OK] Migrated ${MOUNT_POINT}/${BASE_PATH}/${sub_path}"
    fi
}

MIGRATED=0

if [[ -n "${OPENAI_API_KEY:-}" && "${OPENAI_API_KEY}" != "<"* ]]; then
    put_secret "openai" api_key="${OPENAI_API_KEY}"
    MIGRATED=$((MIGRATED + 1))
fi

if [[ -n "${ANTHROPIC_API_KEY:-}" && "${ANTHROPIC_API_KEY}" != "<"* ]]; then
    put_secret "anthropic" api_key="${ANTHROPIC_API_KEY}"
    MIGRATED=$((MIGRATED + 1))
fi

if [[ -n "${GEMINI_API_KEY:-}" && "${GEMINI_API_KEY}" != "<"* ]]; then
    put_secret "gemini" api_key="${GEMINI_API_KEY}"
    MIGRATED=$((MIGRATED + 1))
fi

if [[ -n "${AZURE_OPENAI_API_KEY:-}" && "${AZURE_OPENAI_API_KEY}" != "<"* ]]; then
    put_secret "azure_openai" \
        api_key="${AZURE_OPENAI_API_KEY}" \
        endpoint="${AZURE_OPENAI_ENDPOINT:-}" \
        deployment="${AZURE_OPENAI_DEPLOYMENT:-}"
    MIGRATED=$((MIGRATED + 1))
fi

if [[ -n "${AWS_ACCESS_KEY_ID:-}" && -n "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
    put_secret "aws" \
        access_key_id="${AWS_ACCESS_KEY_ID}" \
        secret_access_key="${AWS_SECRET_ACCESS_KEY}" \
        region="${AWS_REGION:-us-east-1}"
    MIGRATED=$((MIGRATED + 1))
fi

if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
    put_secret "database" \
        "host=${POSTGRES_HOST:-localhost}" \
        "port=${POSTGRES_PORT:-5432}" \
        "name=${POSTGRES_DB:-product_value_system}" \
        "username=${POSTGRES_USER:-postgres}" \
        "password=${POSTGRES_PASSWORD}"
    MIGRATED=$((MIGRATED + 1))
fi

if [[ -n "${ENCRYPTION_KEY:-}" ]]; then
    put_secret "encryption" key="${ENCRYPTION_KEY}"
    MIGRATED=$((MIGRATED + 1))
fi

echo ""
echo "Migration complete (${MIGRATED} secret group(s) processed)."
echo "IMPORTANT: Rotate all migrated credentials at each provider and remove ${ENV_FILE} after verification."
