#!/usr/bin/env bash
# Preserve Linux namespace-policy evidence; never disable AppArmor or the jailer.
set -euo pipefail
report_dir="${1:?diagnostic directory required}"
mkdir -p "$report_dir"
exec > >(tee "$report_dir/userns-preflight.log") 2>&1
command -v bwrap
readlink -f "$(command -v bwrap)"
bwrap --version
dpkg-query -W bubblewrap apparmor
dpkg-query -L bubblewrap apparmor
cat /proc/self/attr/current
sysctl kernel.apparmor_restrict_unprivileged_userns user.max_user_namespaces
sudo aa-status || true
for profile in /etc/apparmor.d/bwrap /etc/apparmor.d/bwrap-userns-restrict; do
    if [ -f "$profile" ]; then
        printf '\nDistribution profile: %s\n' "$profile"
        cat "$profile"
    fi
done
if bwrap --unshare-user --ro-bind / / -- true; then
    exit 0
fi
sudo dmesg --ctime | tail -n 100 || true
# Some hosted images install package files without loading their shipped policy.
# Load only distribution-owned bwrap policy, then repeat the unchanged probe.
loaded=0
for profile in /etc/apparmor.d/bwrap /etc/apparmor.d/bwrap-userns-restrict; do
    if [ -f "$profile" ]; then
        dpkg-query -S "$profile"
        sudo apparmor_parser -r "$profile"
        loaded=1
    fi
done
if [ "$loaded" -ne 1 ]; then
    printf 'No distribution bwrap profile found; refusing a security bypass.\n'
    exit 1
fi
if ! bwrap --unshare-user --ro-bind / / -- true; then
    sudo dmesg --ctime | tail -n 100 || true
    exit 1
fi
