#!/usr/bin/env bash
# Build and check the archive on the Mac (site/ needs the raw store, so CI cannot build it).
# Deploying is done by CI when site/ is merged to main (.github/workflows/deploy.yml).
#
#   scripts/publish.sh            statify -> seo -> edge script check -> verify_build + check_excludes
#   scripts/publish.sh --no-build skip statify (check the site/ already generated)
#   scripts/publish.sh --deploy   emergency only: also deploy from the Mac, then smoke test
#                                 (needs pass-cli logged in; accepts --dry-run, --allow-mass-delete)
#
# Then commit site/ in the same pull request as the change (never `git add -A`).
set -euo pipefail

cd "$(dirname "$0")/.."
build=1 deploy=0 deploy_args=()
for a in "$@"; do
  case "$a" in
    --no-build) build=0 ;;
    --deploy) deploy=1 ;;
    --dry-run) deploy_args+=(--dry-run) ;;
    --allow-mass-delete) deploy_args+=(--allow-mass-delete) ;;
    *) echo "usage: $0 [--no-build] [--deploy [--dry-run] [--allow-mass-delete]]"; exit 1 ;;
  esac
done

if [ "$build" = 1 ]; then
  python3 -I scripts/statify.py
fi

# sitemap.xml and robots.txt (also written by seo.py when run alone; idempotent, so --no-build gets them too)
python3 -I scripts/seo.py

# The edge script must match build-meta/url-map.json (regenerate, commit, then tofu apply).
python3 -I scripts/gen_edge_script.py --check
node scripts/test_edge_script.mjs

python3 -I scripts/verify_build.py
python3 -I scripts/check_excludes.py

if [ "$deploy" = 1 ]; then
  pass-cli run --env-file infra/secrets.env -- python3 -I scripts/deploy.py ${deploy_args[@]+"${deploy_args[@]}"}
  if [[ " ${deploy_args[*]-} " != *" --dry-run "* ]]; then
    python3 -I scripts/smoke_test.py --sample 300
  fi
else
  echo "Checks passed. Commit site/ in the pull request; merging to main deploys it."
fi
