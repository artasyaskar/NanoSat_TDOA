#!/bin/bash
# collect_captures.sh - run on the LAPTOP to copy every node's recordings into ./captures
# Default lab IP addresses for Station 1 (V3), Station 2 (V3), and Station 3 (V4):
PI_USER=${PI_USER:-pi}
NODES=(
  "node1:${PI_USER}@192.168.5.248"
  "node2:${PI_USER}@192.168.5.249"
  "node3:${PI_USER}@192.168.5.250"
)
mkdir -p captures
for entry in "${NODES[@]}"; do
  name=${entry%%:*}; host=${entry#*:}
  echo "Copying from $name ($host) ..."
  scp -o ConnectTimeout=5 "$host:~/tdoa/captures/${name}_*" captures/ || echo "  !! could not reach $name ($host) - check IP/username or copy via USB"
done
ls -1 captures | wc -l | xargs echo "Files now in ./captures:"
