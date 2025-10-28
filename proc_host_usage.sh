#!/usr/bin/env bash
# Show CPU & MEM usage of a target process (and its children)
# as a percentage of the whole host. Default target: python.*main\.py
# Usage:
#   ./proc_host_usage.sh               # uses default pattern
#   ./proc_host_usage.sh <PID|pattern> # specify PID or regex pattern
# Env:
#   INTERVAL=2 ./proc_host_usage.sh    # seconds between updates
#   ONESHOT=1  ./proc_host_usage.sh    # print once and exit

set -u

TARGET="${1:-python.*main\.py}"
INTERVAL="${INTERVAL:-1}"
CORES="$(nproc)"

have_pgrep() { command -v pgrep >/dev/null 2>&1; }
have_pgrep || { echo "pgrep not found. Install psmisc (sudo apt-get install -y psmisc)"; exit 1; }

# Return a comma-separated list of PIDs for TARGET, including children (recursive)
get_pids() {
  local arg="$1" pids="" new="" kids="" all=""
  if [[ "$arg" =~ ^[0-9]+$ ]]; then
    pids="$arg"
  else
    pids="$(pgrep -f "$arg" || true)"
  fi
  [[ -z "$pids" ]] && { echo ""; return; }

  all="$pids"
  new="$pids"
  while [[ -n "$new" ]]; do
    kids=""
    for p in $new; do
      # direct children of each PID
      this_kids="$(pgrep -P "$p" || true)"
      [[ -n "$this_kids" ]] && kids="$kids $this_kids"
    done
    kids="$(echo "$kids")"
    [[ -z "$kids" ]] && break
    all="$all $kids"
    new="$kids"
  done

  echo "$all" | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u | tr '\n' ',' | sed 's/,$//'
}

print_header() {
  printf "%-19s  %-8s  %-10s  %-10s  %s\n" "TIME" "PIDS" "CPU(host%)" "MEM(%)" "RSS(MiB)"
}

now() { date +"%F %T"; }

run_once() {
  local pids="$1"
  if [[ -z "$pids" ]]; then
    printf "%s  %s\n" "$(now)" "No matching PIDs found."
    return
  fi

  # CPU: ps %cpu is per-core %. Sum and divide by #cores -> % of host.
  local cpu_sum host_cpu
  cpu_sum="$(ps -o %cpu= -p "$pids" 2>/dev/null | awk '{s+=$1} END{printf("%.2f", s+0)}')"
  host_cpu="$(awk -v s="$cpu_sum" -v c="$CORES" 'BEGIN{printf "%.2f", (s/c)}')"

  # MEM: ps %mem is already percent of total RAM. Sum across PIDs.
  local mem_sum
  mem_sum="$(ps -o %mem= -p "$pids" 2>/dev/null | awk '{s+=$1} END{printf("%.2f", s+0)}')"

  # RSS: resident set size (kB) -> MiB
  local rss_kb rss_mib
  rss_kb="$(ps -o rss= -p "$pids" 2>/dev/null | awk '{s+=$1} END{print s+0}')"
  rss_mib="$(awk -v k="$rss_kb" 'BEGIN{printf "%.1f", k/1024}')"

  printf "%-19s  %-8s  %-10s  %-10s  %s\n" "$(now)" "${pids//,/ }" "$host_cpu" "$mem_sum" "$rss_mib"
}

main_loop() {
  print_header
  while :; do
    pids="$(get_pids "$TARGET")"
    run_once "$pids"
    [[ "${ONESHOT:-0}" = "1" ]] && break
    sleep "$INTERVAL"
  done
}

main_loop
