#!/bin/sh
# Build a fictional, disposable setup for recording the tour (see README.md).
# usage: scripts/tour/fixture.sh DIR   (DIR must not exist)
set -eu
F=${1:?usage: fixture.sh DIR}
[ -e "$F" ] && { echo "$F exists; choose a new folder" >&2; exit 1; }
mkdir -p "$F/seed" "$F/home/work/backend/.kiro/skills/sql-explain" "$F/zip/commit-message"
skill() { mkdir -p "$1/$2"; printf -- "---\nname: %s\ndescription: %s\n---\n# %s\n" "$2" "$3" "$2" > "$1/$2/SKILL.md"; }
cd "$F/seed"
git init -q -b main
skill ip/skills plan "Breaks a feature into reviewed steps before coding."
skill ip/skills review "Read-only code review of the current diff."
skill ip/skills adr-writer "Drafts an architecture decision record from a discussion."
mkdir -p ip/agents profiles
echo '{"name":"reviewer","description":"Read-only review agent."}' > ip/agents/reviewer.json
printf "skills: plan review\n" > profiles/starter.manifest
git add -A && git -c user.name=Jonas -c user.email=jonas@example.com -c commit.gpgsign=false commit -qm "Start the team library"
skill ip/skills pr-review "Reviews a pull request against the team checklist and drafts comments."
git add -A && git -c user.name=Maria -c user.email=maria@example.com -c commit.gpgsign=false commit -qm "Share pr-review"
git clone -q --bare "$F/seed" "$F/team.git"
skill "$F/home/work/backend/.kiro/skills" sql-explain "Explains a slow query plan and suggests indexes."
skill "$F/zip" commit-message "Writes a conventional commit message from the staged diff."
(cd "$F/zip" && zip -qr "$F/commit-message.zip" commit-message)
echo "Start: CAPTAIN_SHARE_HOME=$F/home/.captain captain-share --target $F/home/work/backend serve"
