#!/usr/bin/env bash
# Reuses the user's existing, pinned throwvm harness instead of creating a new one.
set -euo pipefail
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
vm_dir=${VM_DIR:?Set VM_DIR to a VM cache directory with sufficient disk space}
vm_work="$vm_dir/thermal-monitor-test"
throwvm=${THROWVM:-"$HOME/code/throwvm/throwvm.py"}
[[ -f "$throwvm" ]] || { echo 'Set THROWVM to the existing throwvm.py harness.' >&2; exit 1; }
[[ ! -e "$vm_work/vm.json" ]] || { echo 'VM directory already exists; inspect it before reuse.' >&2; exit 1; }
mkdir -p "$project_root/target/vm-test"
cleanup() {
    local status=$?
    if [[ -f "$vm_work/vm.json" ]]; then
        if [[ "$status" == 0 ]]; then python3 -I "$throwvm" down "$vm_work" --rm
        else python3 -I "$throwvm" down "$vm_work" --failed; fi
    fi
}
trap cleanup EXIT
vm_env=$(python3 -I "$throwvm" up "$vm_work" --vm-dir "$vm_dir" --name thermal-monitor \
    --hostname thermal-test --mem 3072 --cpus 2 --owner "$$" --provision "$project_root/tests/vm/provision.sh")
eval "$vm_env"
ssh_cmd=("${VM_SSH[@]}")
"${ssh_cmd[@]}" 'mkdir -p ~/thermal-test'
tar -C "$project_root" -czf - scripts tests | "${ssh_cmd[@]}" 'tar -xzf - -C ~/thermal-test'
"${ssh_cmd[@]}" 'cat > ~/thermal-test/thermal-monitor && chmod +x ~/thermal-test/thermal-monitor' \
    < "$project_root/target/release/thermal-monitor"
"${ssh_cmd[@]}" 'bash ~/thermal-test/tests/vm/guest.sh' | tee "$project_root/target/vm-test/checks.log"
"${ssh_cmd[@]}" 'tar -C ~/thermal-test/results -cf - .' | tar -xf - -C "$project_root/target/vm-test"
