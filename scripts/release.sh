#!/usr/bin/env bash
# Publish a version: annotated git tag + GitHub release, both with that version's CHANGELOG.md section.
#   scripts/release.sh v0.28.0            # tag the current commit
#   scripts/release.sh v0.24.0 ea54038    # tag an older commit
# Needs push access to the repo. The GitHub release needs the GitHub CLI (https://cli.github.com,
# then `gh auth login`); without it the tag is pushed and the script prints the page to finish by hand.
set -euo pipefail
cd "$(dirname "$0")/.."
ver="${1:?usage: scripts/release.sh vX.Y.Z [commit]}"
commit="${2:-HEAD}"
notes="$(awk -v v="## $ver " 'index($0, v) == 1 {on = 1; next} /^## / {on = 0} on' CHANGELOG.md \
         | sed -e '/./,$!d' | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}')"
[ -n "$notes" ] || { echo "No '## $ver ' section in CHANGELOG.md" >&2; exit 1; }
title="$(grep -m1 "^## $ver " CHANGELOG.md | sed -E 's/^## [^ ]+ \([^)]*\): //')"

if git rev-parse -q --verify "refs/tags/$ver" >/dev/null; then
  echo "Tag $ver already exists"
else
  git tag -a "$ver" "$commit" -m "$ver: $title" -m "$notes"
  echo "Tagged $ver at $(git rev-parse --short "$commit")"
fi
git push origin "refs/tags/$ver"

if command -v gh >/dev/null 2>&1; then
  if gh release view "$ver" >/dev/null 2>&1; then
    echo "Release $ver already exists"
  else
    gh release create "$ver" --title "$ver: $title" --notes "$notes" --verify-tag
  fi
else
  url="$(git remote get-url origin | sed -E 's#git@github.com:#https://github.com/#; s#\.git$##')"
  echo "GitHub CLI not installed: create the release at $url/releases/new?tag=$ver"
  echo "(title: $ver: $title; paste the $ver section of CHANGELOG.md as the description)"
fi
