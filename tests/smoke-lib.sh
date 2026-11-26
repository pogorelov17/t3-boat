# Helpers for smoke-image.sh, sourced by it and by tests/test_smoke_lib.py.
#
# Output is captured in full before it is matched. Under pipefail, `docker logs ... | grep -q` fails whenever grep
# exits at its first match while docker is still writing (SIGPIPE), so a pass depended on the log's length. A
# captured command's own failure, such as a docker error, still stops the test.

# Fail unless TEXT contains NEEDLE; LABEL names the text in the message.
require() {  # LABEL TEXT NEEDLE
  [[ $2 == *"$3"* ]] && return 0
  printf '%s\n' "$2" >&2
  echo "$1 lacks: $3" >&2
  return 1
}

# Print the container's whole log, stdout and stderr; a failure of docker logs itself is returned with its message.
container_logs() {  # CONTAINER
  local output status=0
  output=$(docker logs "$1" 2>&1) || status=$?
  if ((status)); then
    printf '%s\n' "$output" >&2
    echo "docker logs $1 failed (exit $status)" >&2
    return "$status"
  fi
  printf '%s\n' "$output"
}

# The last line t3-boat status prints when setup is incomplete.
SETUP_HINT='Next: docker exec -it t3-boat t3-boat setup'

# Print the output of t3-boat status. It exits 1 while setup is incomplete, which is expected on an offline
# volume; any other status, such as docker exec's own 125 to 127, is returned.
status_output() {  # CONTAINER
  local output status=0
  output=$(docker exec "$1" t3-boat status) || status=$?
  if ((status == 1)) && [[ ${output##*$'\n'} == "$SETUP_HINT" ]]; then
    status=0
  fi
  if ((status)); then
    printf '%s\n' "$output" >&2
    echo "docker exec $1 t3-boat status failed (exit $status)" >&2
    return "$status"
  fi
  printf '%s\n' "$output"
}

# Print the output of t3-boat status, which must report an incomplete setup: exit 1 with SETUP_HINT as its last
# line. A ready status fails; so does exit 1 without the hint, such as an error message from a crash; any other
# status, such as docker exec's own 125 to 127, is returned.
incomplete_status() {  # CONTAINER
  local output status=0
  output=$(docker exec "$1" t3-boat status) || status=$?
  if ((status == 1)) && [[ ${output##*$'\n'} == "$SETUP_HINT" ]]; then
    printf '%s\n' "$output"
    return 0
  fi
  printf '%s\n' "$output" >&2
  if ((status == 0)); then
    echo "docker exec $1 t3-boat status reported a ready setup; expected exit 1 and: $SETUP_HINT" >&2
    return 1
  fi
  if ((status == 1)); then
    echo "docker exec $1 t3-boat status exited 1 without its last line: $SETUP_HINT" >&2
    return 1
  fi
  echo "docker exec $1 t3-boat status failed (exit $status)" >&2
  return "$status"
}
