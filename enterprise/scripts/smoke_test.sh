#!/usr/bin/env bash
# HTTP smoke tests against a running AduPort API (acceptance B1–B6 subset).
# Usage: ./smoke_test.sh [base_url] [api_key]
set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"
KEY="${2:-${ADUPORT_API_KEY:-}}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

AUTH=()
if [[ -n "$KEY" ]]; then
  AUTH=(-H "X-API-Key: $KEY")
fi

echo "== B1 health =="
curl -sf "${AUTH[@]}" "$BASE/api/health" | grep -q '"status"' 
echo "  ok"

echo "== B6 formats export_only =="
FMTS="$(curl -sf "${AUTH[@]}" "$BASE/api/formats")"
echo "$FMTS" | grep -q 'export_only'
echo "$FMTS" | grep -q 'azw3'
echo "  ok"

echo "== B2 Chinese MD → PDF =="
printf '%s\n' '# 中文标题测试' '' '这是中文段落内容。' > "$TMP/in.md"
HTTP=$(curl -sS -o "$TMP/out.pdf" -w "%{http_code}" "${AUTH[@]}" \
  -F "file=@$TMP/in.md;filename=in.md" \
  -F "source_format=md" \
  -F "target_format=pdf" \
  "$BASE/api/convert")
[[ "$HTTP" == "200" ]] || { echo "convert pdf failed HTTP $HTTP" >&2; exit 1; }
# Identity-H or substantial binary; reject tiny Helvetica-only garbage
SIZE=$(wc -c < "$TMP/out.pdf" | tr -d ' ')
[[ "$SIZE" -gt 1500 ]] || { echo "PDF too small ($SIZE)" >&2; exit 1; }
if grep -a -q '?????' "$TMP/out.pdf" 2>/dev/null; then
  echo "PDF contains ????? markers" >&2
  exit 1
fi
echo "  ok ($SIZE bytes)"

echo "== B3 MD → DOCX =="
HTTP=$(curl -sS -o "$TMP/out.docx" -w "%{http_code}" "${AUTH[@]}" \
  -F "file=@$TMP/in.md;filename=in.md" \
  -F "source_format=md" \
  -F "target_format=docx" \
  "$BASE/api/convert")
[[ "$HTTP" == "200" ]] || { echo "convert docx failed HTTP $HTTP" >&2; exit 1; }
SIZE=$(wc -c < "$TMP/out.docx" | tr -d ' ')
[[ "$SIZE" -gt 1000 ]] || { echo "DOCX too small" >&2; exit 1; }
echo "  ok ($SIZE bytes)"

echo "All smoke checks passed against $BASE"
