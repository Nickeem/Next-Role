#!/usr/bin/env bash
# End-to-end demo (assessment, then review) against a running API, using the synthetic examples.
#   API_URL (default http://localhost:8000) and NEXT_ROLE_API_KEY (or .env) must be set.
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ -z "${NEXT_ROLE_API_KEY:-}" && -f .env ]]; then
  NEXT_ROLE_API_KEY="$(grep -E '^NEXT_ROLE_API_KEY=' .env | cut -d= -f2-)"
fi
API_URL="${API_URL:-http://localhost:8000}"
: "${NEXT_ROLE_API_KEY:?set NEXT_ROLE_API_KEY}"

call() {  # call METHOD PATH [JSON_FILE_OR_STRING]
  local method=$1 path=$2 data=${3:-}
  local args=(-sS --fail-with-body -X "$method" "$API_URL$path" -H "X-API-Key: $NEXT_ROLE_API_KEY")
  if [[ -n "$data" ]]; then
    if [[ -f "$data" ]]; then args+=(-H 'Content-Type: application/json' --data-binary "@$data")
    else args+=(-H 'Content-Type: application/json' -d "$data"); fi
  fi
  curl "${args[@]}"
}
field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

echo "== Readiness";       curl -sS "$API_URL/health/ready"; echo
echo "== Create candidate"
CANDIDATE_ID=$(call POST /api/v1/candidates examples/candidate.json | field "['id']")
echo "candidate_id=$CANDIDATE_ID"

echo "== Submit job"
JOB_ID=$(call POST /api/v1/jobs examples/jobs/senior-backend-engineer.json | field "['job']['id']")
echo "job_id=$JOB_ID"
echo "== Resubmit the same job (deduplicated)"
call POST /api/v1/jobs examples/jobs/senior-backend-engineer.json | field "['outcome']"

echo "== Assess"
ASSESSMENT=$(call POST "/api/v1/jobs/$JOB_ID/assessments" "{\"candidate_id\": \"$CANDIDATE_ID\"}")
ASSESSMENT_ID=$(echo "$ASSESSMENT" | field "['assessment']['id']")
echo "$ASSESSMENT" | python3 scripts/print_assessment.py
echo "== Retrieve stored assessment"
call GET "/api/v1/assessments/$ASSESSMENT_ID" | field "['tier']"

echo "== Review opened by the assessment (Phase 2)"
REVIEW=$(call GET "/api/v1/reviews?candidate_id=$CANDIDATE_ID&status=pending_review&status=shortlisted&status=postponed&status=rejected&status=approved_for_drafting" \
  | python3 -c "import json,sys; items=[i for i in json.load(sys.stdin)['items'] if i['job_id']=='$JOB_ID']; print(items[0]['id'], items[0]['version'], items[0]['status'])")
read -r REVIEW_ID REVIEW_VERSION REVIEW_STATUS <<<"$REVIEW"
echo "review_id=$REVIEW_ID status=$REVIEW_STATUS version=$REVIEW_VERSION"

if [[ "$REVIEW_STATUS" == "pending_review" ]]; then
  echo "== Shortlist it (no documents are generated)"
  call POST "/api/v1/reviews/$REVIEW_ID/decisions" \
    "{\"action\": \"shortlist\", \"expected_version\": $REVIEW_VERSION}" | field "['status']"
fi
echo "== Review history"
call GET "/api/v1/reviews/$REVIEW_ID/events" \
  | python3 -c "import json,sys; [print(e['created_at'][:19], e['actor'], e['event_type'], '->', e['new_status']) for e in json.load(sys.stdin)]"
