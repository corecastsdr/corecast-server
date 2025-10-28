#!/usr/bin/env bash
C="${1:?usage: $0 <container-name>}"
PID=$(docker inspect -f '{{.State.Pid}}' "$C") || { echo "no such container"; exit 1; }
CG="/sys/fs/cgroup$(awk -F: '/^0::/{print $3}' /proc/$PID/cgroup)"

# CPU over 1s -> cores used, then % of host
U1=$(awk '/usage_usec/{print $2}' "$CG/cpu.stat"); sleep 1
U2=$(awk '/usage_usec/{print $2}' "$CG/cpu.stat")
CORES_USED=$(awk -v du=$((U2-U1)) 'BEGIN{printf "%.3f", du/1e6}')
HOST_CORES=$(nproc)
CPU_PCT=$(awk -v c="$CORES_USED" -v h="$HOST_CORES" 'BEGIN{printf "%.2f", (c/h)*100}')

# Memory -> % of host
MEM_BYTES=$(cat "$CG/memory.current")
HOST_BYTES=$(awk '/MemTotal/{print $2*1024}' /proc/meminfo)
MEM_PCT=$(awk -v m="$MEM_BYTES" -v h="$HOST_BYTES" 'BEGIN{printf "%.2f", (m/h)*100}')

printf "%s  CPU: ~%s%% of host (%s cores)\n" "$C" "$CPU_PCT" "$CORES_USED"
printf "%s  MEM: ~%s%% of host\n" "$C" "$MEM_PCT"
